#!/bin/bash
# ---------------------------------------------------------------------------
# finance-viz one-shot launcher
#
#   ./start.sh              refresh Plaid + Shareworks, then open the dashboard
#   ./start.sh --fast       skip all refresh, open cached data instantly
#   ./start.sh --no-shareworks   refresh Plaid only (don't touch Chrome)
#   ./start.sh --demo       generated sample data, no credentials/Plaid/Chrome
#
# Opens http://localhost:5000 in the *real Default Chrome profile* — the one
# logged into Shareworks. See RUN.md for the full story.
# ---------------------------------------------------------------------------
set -uo pipefail
cd "$(dirname "$0")"
source venv/bin/activate

# --- config ----------------------------------------------------------------
# Every value here can be overridden in ~/.finance-viz/start.env (sourced below).
CHROME_EXE='/mnt/c/Program Files/Google/Chrome/Application/chrome.exe'
CHROME_WIN='C:\Program Files\Google\Chrome\Application\chrome.exe'
USER_DATA=''            # Chrome user-data dir; auto-derived from %LOCALAPPDATA% if empty
PROFILE='Default'
SW_URL='https://shareworks.solium.com/solium/servlet/ui/dashboard'
DASH_URL='http://localhost:5000'
DEBUG_PORT=9222

# Machine-specific overrides live outside the repo.
[ -f "$HOME/.finance-viz/start.env" ] && source "$HOME/.finance-viz/start.env"

# WSL: derive the Windows Chrome profile dir from %LOCALAPPDATA% so no
# username is hardcoded anywhere.
if [ -z "$USER_DATA" ] && command -v powershell.exe >/dev/null 2>&1; then
    _LOCALAPPDATA=$(powershell.exe -NoProfile -Command '$env:LOCALAPPDATA' 2>/dev/null | tr -d '\r')
    [ -n "$_LOCALAPPDATA" ] && USER_DATA="${_LOCALAPPDATA}\\Google\\Chrome\\User Data"
fi

FAST=0
SKIP_SW=0
DEMO=0
for arg in "$@"; do
    case "$arg" in
        --fast|--no-refresh) FAST=1 ;;
        --no-shareworks)     SKIP_SW=1 ;;
        --demo)              DEMO=1 ;;
        *) echo "unknown flag: $arg" ;;
    esac
done

if [ "$DEMO" -eq 1 ]; then
    DEMO_DIR="${TMPDIR:-/tmp}/finance-viz-demo"
    echo "Demo mode — generating sample data in $DEMO_DIR..."
    python3 scripts/gen_sample_data.py "$DEMO_DIR" >/dev/null
    export FINANCE_VIZ_DIR="$DEMO_DIR"
    SW_STATUS="demo"
fi

# --- helpers ---------------------------------------------------------------
chrome_up() {
    curl -s --connect-timeout 2 "http://localhost:${DEBUG_PORT}/json/version" >/dev/null 2>&1
}

# Ensure Chrome is running on the Default profile + debug port.
# Reuse a live session if the port is already up (don't disrupt open windows).
ensure_chrome() {
    if chrome_up; then
        echo "  Chrome debug port already up — reusing session."
        return 0
    fi
    if [ -z "$USER_DATA" ]; then
        echo "  Chrome user-data dir unknown (set USER_DATA in ~/.finance-viz/start.env) — Shareworks will use cached data."
        return 1
    fi
    echo "  Chrome not on :${DEBUG_PORT} — force-quitting all Chrome to grab the port."
    echo "  (this closes your open Chrome windows)"
    # The debug port only binds when NO Chrome owns the Default profile. A single
    # kill+sleep isn't enough: background apps / lingering processes can survive
    # or respawn, and the relaunch then gets swallowed ("Opening in existing
    # browser session") without ever binding 9222. So loop the kill until the
    # process count is verifiably 0 before launching.
    local count
    count=$(powershell.exe -NoProfile -Command \
        "for (\$i=0; \$i -lt 8; \$i++) { Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 800; \$c = (Get-Process chrome -ErrorAction SilentlyContinue | Measure-Object).Count; if (\$c -eq 0) { break } }; (Get-Process chrome -ErrorAction SilentlyContinue | Measure-Object).Count" 2>/dev/null | tr -d '\r\n ')
    if [ "${count:-1}" != "0" ]; then
        echo "  Couldn't fully close Chrome (still ${count:-?} running) — Shareworks will use cached data."
        return 1
    fi
    echo "  Chrome fully closed. Launching on Default profile + debug port..."
    powershell.exe -NoProfile -Command \
        "Start-Process -FilePath '${CHROME_WIN}' -ArgumentList '--remote-debugging-port=${DEBUG_PORT}','--user-data-dir=${USER_DATA}','--profile-directory=${PROFILE}','${SW_URL}'" >/dev/null 2>&1
    echo "  waiting for debug port..."
    curl -s --retry 25 --retry-delay 1 --retry-connrefused --connect-timeout 2 \
        --max-time 45 "http://localhost:${DEBUG_PORT}/json/version" >/dev/null 2>&1
    if chrome_up; then echo "  Chrome up on :${DEBUG_PORT}."; return 0
    else echo "  Chrome didn't bind the port — Shareworks will use cached data."; return 1; fi
}

# Open the dashboard in the Default-profile Chrome when present (WSL), else
# whatever opener the platform has, else just print the URL.
open_dashboard() {
    if [ -x "$CHROME_EXE" ]; then
        "$CHROME_EXE" --profile-directory="$PROFILE" "$DASH_URL" >/dev/null 2>&1 &
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$DASH_URL" >/dev/null 2>&1 &
    elif command -v open >/dev/null 2>&1; then
        open "$DASH_URL" >/dev/null 2>&1 &
    else
        echo "  Open $DASH_URL in your browser."
    fi
}

# Run the Shareworks (equity providers) refresh once. Returns 0 if the session
# was live, 1 if it expired/failed. run_providers() logs a provider error rather
# than exiting non-zero, so a plain exit code isn't enough — we grep the output.
sw_refresh() {
    local out
    out=$(timeout 90 python3 refresh.py --providers --no-news 2>&1)
    echo "$out"
    if echo "$out" | grep -qi "not logged into Shareworks\|Provider 'Shareworks' failed"; then
        return 1
    fi
    return 0
}

# Refresh Shareworks, and if the session expired, hold the interactive login
# loop the /finance skill used to run: prompt, wait, retry until live or skipped.
# In a non-interactive context (cron/background) there's no tty to prompt on, so
# we just fall back to cached data — same as before.
handle_shareworks() {
    echo "Refreshing Shareworks..."
    ensure_chrome
    if sw_refresh; then SW_STATUS="live"; return; fi

    SW_STATUS="cached"
    if ! [ -t 0 ]; then
        echo "  Shareworks refresh failed/expired — no terminal to log in on; using cached data."
        return
    fi
    while :; do
        echo ""
        echo "  Shareworks session expired. A Chrome window is open on the Morgan Stanley /"
        echo "  Shareworks login page. Log in (keep 'Remember my account' checked, finish any"
        echo "  2FA), land on the dashboard, then press Enter to retry — or type 's' + Enter"
        echo "  to skip and use cached data."
        read -r reply
        if [ "$reply" = "s" ] || [ "$reply" = "S" ]; then
            echo "  Skipping Shareworks — using cached data."
            return
        fi
        echo "  Re-checking Shareworks login..."
        if sw_refresh; then
            SW_STATUS="live"
            echo "  Shareworks is live now."
            return
        fi
        echo "  Still not logged into Shareworks — try again."
    done
}

# Print the net-worth summary the skill used to fetch (steps 4-5).
report() {
    echo ""
    echo "  === Net worth ==="
    NW_JSON="$(curl -s "$DASH_URL/api/networth")" python3 <<'PY' 2>/dev/null || echo "  (could not fetch net worth summary)"
import json, os, sys
try:
    d = json.loads(os.environ.get("NW_JSON", ""))
except Exception:
    print("  (could not fetch net worth summary)"); sys.exit(0)
print(f"  Total: ${d['total']:,.2f}")
for a in d['accounts']:
    print(f"    {a['account_name']}: ${a['current_value']:,.2f}")
PY
    echo "  Shareworks: ${SW_STATUS:-skipped}"
}

# --- 1. build the dashboard CSS first, so the old server keeps serving while
# we do slow work — the :5000 downtime window is just kill→Flask-boot ---------
if [ -x ./bin/tailwindcss ]; then
    echo "Building CSS..."
    ./bin/tailwindcss -i static/src/input.css -o static/app.css --minify 2>/dev/null \
        || echo "  CSS build failed — using committed static/app.css."
else
    echo "  Tailwind CLI not found (run ./scripts/setup-tailwind.sh) — using committed static/app.css."
fi

# --- 1b. stop any existing server -------------------------------------------
if lsof -ti:5000 >/dev/null 2>&1; then
    echo "Stopping existing server on :5000..."
    lsof -ti:5000 | xargs kill 2>/dev/null
fi

# --- 2. start Flask in the background (serves cached data immediately) ------
echo "Starting dashboard server..."
python3 app.py >/tmp/finance-viz-server.log 2>&1 &
APP_PID=$!
trap 'kill $APP_PID 2>/dev/null' INT TERM EXIT

# --- 3. refresh data (overlaps with server boot) ---------------------------
if [ "$DEMO" -eq 1 ]; then
    echo "Demo mode — serving generated sample data, no refresh."
elif [ "$FAST" -eq 1 ]; then
    echo "Fast mode — skipping refresh, showing cached data."
else
    echo "Refreshing Plaid data..."
    python3 refresh.py --plaid || echo "  Plaid refresh failed — using cached data."

    if [ "$SKIP_SW" -eq 0 ]; then
        handle_shareworks
        # Walgreens Rx fills (splits pharmacy vs household at Walgreens) — reuses
        # the same Chrome session. Harmless/skipped if disabled or not logged in.
        echo "Refreshing Walgreens Rx..."
        timeout 90 python3 refresh.py --walgreens --no-news \
            || echo "  Walgreens refresh failed/expired — using cached data. Log into walgreens.com in Chrome and rerun."
    fi
fi

# --- 4. wait for the server, then open in the right Chrome profile ---------
echo "Waiting for server..."
curl -s --retry 30 --retry-delay 1 --retry-connrefused --connect-timeout 1 \
    --max-time 35 "$DASH_URL/api/networth" >/dev/null 2>&1
open_dashboard

report

echo ""
echo "  Dashboard live at $DASH_URL (opened in Chrome '$PROFILE' profile)"
echo "  Ctrl-C to stop the server."
echo ""

# Keep the script attached to the server so Ctrl-C cleans up.
wait $APP_PID
