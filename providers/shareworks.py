"""
Solium Shareworks provider — scrapes RSU/stock plan dashboard via Playwright.

Auth strategy (tried in order):
  A. Stealth automated login — playwright-stealth patches browser fingerprints
     to bypass Akamai bot detection. Credentials from 1Password.
  B. Saved session fallback — if stealth login fails or session file exists,
     load saved cookies and skip login entirely. When the session expires,
     the script opens a visible browser window for you to log in manually,
     then saves the new session.

Config block in ~/.finance-viz/config.json:
  {
    "type":           "shareworks",
    "1password_item": "Shareworks",
    "url":            "https://shareworks.solium.com/solium/servlet/userLogin.do",
    "symbol":         "ACME"
  }

Debug mode:
    Set "debug": true in the config block, or use debug_shareworks.py.
    Saves a screenshot to ~/.finance-viz/shareworks_debug.png after login.
"""

import re, time, json, os, stat
from datetime import date, datetime
from pathlib import Path
from .base import BaseProvider, ProviderError

DEBUG_SCREENSHOT = Path.home() / ".finance-viz" / "shareworks_debug.png"
SESSION_FILE     = Path.home() / ".finance-viz" / "shareworks_session.json"


class ShareworksProvider(BaseProvider):
    name = "shareworks"

    def fetch(self) -> dict:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        item   = self.config.get("1password_item", "Shareworks")
        url    = self.config.get("url", "https://shareworks.solium.com/solium/servlet/userLogin.do")
        symbol = self.config.get("symbol", "")
        debug  = self.config.get("debug", False)

        with sync_playwright() as pw:
            # Option B first: connect to user's real Chrome (no credentials needed)
            result = self._session_fallback(pw, url, symbol, debug)
            if result:
                return result

            # Option A fallback: stealth automated login (needs credentials)
            if debug:
                print("[debug] CDP unavailable — trying stealth login")
            user_override = self.config.get("_user_override", "")
            pass_override = self.config.get("_pass_override", "")
            if user_override and pass_override:
                username, password = user_override, pass_override
            else:
                try:
                    username = self.op_field(item, "username")
                    password = self.op_field(item, "password")
                except Exception as e:
                    raise ProviderError(
                        f"CDP connection failed and 1Password unavailable: {e}\n"
                        "Either launch Chrome with --remote-debugging-port=9222 and log in, "
                        "or sign into 1Password CLI first."
                    )
            return self._try_stealth_login(pw, url, username, password, symbol, debug)

    # ------------------------------------------------------------------
    # Option A — stealth automated login
    # ------------------------------------------------------------------

    def _try_stealth_login(self, pw, url, username, password, symbol, debug):
        from playwright.sync_api import TimeoutError as PWTimeout
        try:
            from playwright_stealth import Stealth
        except ImportError:
            if debug:
                print("[debug] playwright-stealth not installed, skipping Option A")
            return None

        try:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx  = browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            stealth = Stealth(navigator_user_agent_override=
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36")
            page = ctx.new_page()
            stealth.use_sync(page)

            page.goto(url, timeout=30_000)
            page.wait_for_load_state("domcontentloaded", timeout=20_000)
            page.wait_for_selector("input", state="attached", timeout=15_000)

            page.fill('input[name="account_number"]', username)
            page.fill('input[name="password"]', password)
            page.click('#submit-btn')

            if debug:
                print("[debug] Option A: submitted login, polling for redirect (up to 45s)...")

            for _ in range(45):
                time.sleep(1)
                title = page.title().lower()
                if "challenge" not in title and "login" not in title and "userlogin" not in page.url.lower():
                    if debug:
                        print(f"[debug] Option A: logged in — {page.title()} @ {page.url}")
                    break
            else:
                if debug:
                    print("[debug] Option A: still blocked after 45s, returning None")
                browser.close()
                return None

            page.wait_for_load_state("domcontentloaded", timeout=15_000)
            # Navigate to dashboard for full dollar values (awards page only has unit counts)
            dashboard_url = "https://shareworks.solium.com/solium/servlet/ui/dashboard"
            if "dashboard" not in page.url.lower():
                page.goto(dashboard_url, timeout=30_000)
                page.wait_for_load_state("domcontentloaded", timeout=20_000)
                time.sleep(4)
            result = self._scrape_portfolio(page, symbol, debug)
            browser.close()
            return result

        except Exception as e:
            if debug:
                print(f"[debug] Option A error: {e}")
            try:
                browser.close()
            except Exception:
                pass
            return None

    # ------------------------------------------------------------------
    # Option B — connect to user's real Chrome via CDP
    # ------------------------------------------------------------------
    # One-time setup: launch Chrome with --remote-debugging-port=9222
    # Log into Shareworks in that Chrome window, then run refresh.
    #
    # Windows shortcut target:
    #   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
    # ------------------------------------------------------------------

    def _session_fallback(self, pw, url, symbol, debug):
        cdp_port = self.config.get("cdp_port", 9222)
        # Try loopback first. In WSL2 mirrored mode this reaches the Windows
        # host's Chrome only when [experimental] hostAddressLoopback=true is set
        # in .wslconfig (Chrome binds DevTools to loopback only and ignores
        # --remote-debugging-address). 127.0.0.1 is tried explicitly because
        # "localhost" resolves to IPv6 ::1 in WSL. The resolv.conf nameserver is
        # a last-ditch fallback (won't help under mirrored networking).
        host_ip  = _wsl2_host_ip()
        candidates = ["localhost", "127.0.0.1"]
        if host_ip and host_ip not in candidates:
            candidates.append(host_ip)

        browser = None
        last_err = None
        for host in candidates:
            cdp_url = f"http://{host}:{cdp_port}"
            if debug:
                print(f"[debug] Option B: trying CDP at {cdp_url}")
            try:
                browser = pw.chromium.connect_over_cdp(cdp_url)
                if debug:
                    print(f"[debug] Option B: connected via {cdp_url}")
                break
            except Exception as e:
                last_err = e

        if browser is None:
            raise ProviderError(
                f"Could not connect to Chrome CDP on port {cdp_port}: {last_err}\n\n"
                "One-time setup — update your Chrome shortcut target to:\n"
                '  chrome.exe --remote-debugging-port=9222 --remote-debugging-address=0.0.0.0\n\n'
                "Then log into Shareworks and navigate to your portfolio page."
            )

        try:
            ctx  = browser.contexts[0] if browser.contexts else browser.new_context()

            # Find any existing Shareworks tab (we'll navigate it to dashboard below)
            page = None
            for p in ctx.pages:
                if "shareworks" in p.url.lower():
                    page = p
                    if debug:
                        print(f"[debug] Option B: found existing Shareworks tab at {p.url}")
                    break

            if page is None:
                page = ctx.new_page()
                if debug:
                    print("[debug] Option B: no Shareworks tab found, opening new one")

            # Always navigate to dashboard — it shows full dollar values.
            # The awards/portfolio pages only show unit counts.
            dashboard_url = "https://shareworks.solium.com/solium/servlet/ui/dashboard"
            if debug:
                print(f"[debug] Option B: navigating to dashboard")
            page.goto(dashboard_url, timeout=30_000)
            page.wait_for_load_state("domcontentloaded", timeout=20_000)
            # Dashboard is a React SPA — wait for data to render
            time.sleep(4)

            if "login" in page.title().lower() or "userlogin" in page.url.lower():
                # Try filling the login form in the user's REAL Chrome with
                # 1Password credentials first. The real profile is a trusted
                # device, so 2FA is usually skipped; a challenge page (or no op
                # access) still falls back to the manual-login flow.
                if self._cdp_login(page, debug):
                    page.goto(dashboard_url, timeout=30_000)
                    page.wait_for_load_state("domcontentloaded", timeout=20_000)
                    time.sleep(4)
                if "login" in page.title().lower() or "userlogin" in page.url.lower():
                    raise ProviderError(
                        "Chrome is open but not logged into Shareworks.\n"
                        "Please log in, then run refresh again."
                    )

            if debug:
                page.screenshot(path=str(DEBUG_SCREENSHOT))
                print(f"[debug] screenshot saved to {DEBUG_SCREENSHOT}")
                print("[debug] page title:", page.title())
                print("[debug] page URL:", page.url)
                print("[debug] page text (first 3000 chars):\n", page.inner_text("body")[:3000])

            return self._scrape_portfolio(page, symbol, debug)

        finally:
            # Do NOT close — this is the user's real Chrome. Calling close() would
            # shut down their browser and drop the Shareworks session cookie,
            # forcing a re-login on every run. Exiting the sync_playwright block
            # drops the CDP connection while leaving Chrome (and the session) open.
            pass

    def _cdp_login(self, page, debug):
        """Fill the Shareworks login form in the user's real Chrome using
        1Password credentials. Returns True once logged in; False falls back
        to the manual flow (2FA challenge, op unavailable, layout change)."""
        item = self.config.get("1password_item", "Shareworks")
        try:
            username = self.op_field(item, "username")
            password = self.op_field(item, "password")
        except Exception as e:
            if debug:
                print(f"[debug] CDP login: 1Password unavailable: {e}")
            return False
        try:
            login_url = self.config.get(
                "url", "https://shareworks.solium.com/solium/servlet/userLogin.do")
            if "userlogin" not in page.url.lower():
                page.goto(login_url, timeout=30_000)
            page.wait_for_selector('input[name="account_number"]', state="visible", timeout=15_000)
            page.fill('input[name="account_number"]', username)
            page.fill('input[name="password"]', password)
            page.click('#submit-btn')
        except Exception as e:
            if debug:
                print(f"[debug] CDP login: form interaction failed: {e}")
            return False
        for _ in range(45):
            time.sleep(1)
            title = page.title().lower()
            if "challenge" in title:
                if debug:
                    print("[debug] CDP login: 2FA challenge — manual login needed")
                return False
            if "login" not in title and "userlogin" not in page.url.lower():
                if debug:
                    print(f"[debug] CDP login: success — {page.title()}")
                return True
        return False

    # ------------------------------------------------------------------
    # Scraping
    # ------------------------------------------------------------------

    def _scrape_portfolio(self, page, symbol, debug):
        body_text = page.inner_text("body")
        note = "Trading window may be closed (blackout period detected)" if "blackout" in body_text.lower() else ""

        # Dashboard shows abbreviated "Available $56.74k" near the top, then full amounts
        # further down as "Available value $56,742.94" — match the full-amount labels first.
        total       = _extract_dollar(body_text, ["Total portfolio value", "Total Portfolio Value", "Total Value", "Portfolio Value"])
        available   = _extract_dollar(body_text, ["Available value", "Available", "Exercisable", "Vested"])
        unavailable = _extract_dollar(body_text, ["Unavailable value", "Unavailable", "Unexercisable", "Unvested", "Restricted"])

        if total == 0 and (available > 0 or unavailable > 0):
            total = available + unavailable
        if total == 0:
            raise ProviderError(
                "Could not find portfolio value on page. "
                "Run with debug=True to inspect the page layout."
            )

        # Prefer the per-vest-event schedule (dates + share counts from the
        # server-rendered grant pages); fall back to the coarse annual chart.
        vesting_schedule = (self._scrape_vesting_events(page, unavailable, debug)
                            or self._scrape_vesting_schedule(page, available, debug))

        return {
            "account_name":  f"Shareworks — {symbol}" if symbol else "Shareworks",
            "current_value": round(total, 2),
            "positions": [
                {
                    "symbol":      symbol,
                    "description": "Vested shares / available to sell",
                    "value":       round(available, 2),
                    "vested":      True,
                },
                {
                    "symbol":      f"{symbol}-RSU" if symbol else "RSU",
                    "description": "Unvested RSUs (pre-tax estimate)",
                    "value":       round(unavailable, 2),
                    "vested":      False,
                },
            ],
            "vesting_schedule": vesting_schedule,
            "as_of": str(date.today()),
            "note":  note,
        }

    def _scrape_vesting_events(self, page, unvested_value, debug):
        """Per-vest-event future schedule from the classic (server-rendered) esoap
        pages — far more reliable than the React SPA and includes share counts.

        Flow: the account-summary servlet lists every grant id for the logged-in
        user; each grant-detail servlet renders a Vest Schedule table. In those
        tables the per-date share count still 'Outstanding' (not yet distributed)
        is the second-to-last column, and future-dated rows are exactly the ones
        with Outstanding > 0. Values are priced so they sum to the unvested total.
        Returns [] on any failure so the caller falls back to the annual chart.
        """
        base = "https://shareworks.solium.com/solium/servlet/"
        summary = base + "userEsoapAccountSummary.do?popupMode=true&embed=true"
        detail = base + "esoapViewGrantDetail?employee_id=%s&employee_grant_id=%s&popupMode=true"
        try:
            page.goto(summary, timeout=30_000)
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
            time.sleep(1.5)
            html = page.content()
            eid_m = re.search(r'employee_id["\'=:&;\s]+(\d{6,})', html)
            if not eid_m:
                if debug:
                    print("[debug] vesting events: no employee_id on account summary")
                return []
            eid = eid_m.group(1)
            gids = sorted(set(re.findall(r'employee_grant_id["\'=:&;\s]+(\d{6,})', html)))
            if not gids:
                if debug:
                    print("[debug] vesting events: no grant ids found")
                return []

            shares_by_date = {}
            for gid in gids:
                page.goto(detail % (eid, gid), timeout=30_000)
                page.wait_for_load_state("domcontentloaded", timeout=15_000)
                time.sleep(1)
                tables = page.eval_on_selector_all(
                    "table",
                    "ts => ts.map(t => Array.from(t.querySelectorAll('tr'))"
                    ".map(r => Array.from(r.querySelectorAll('td,th')).map(c => (c.innerText||'').trim())))",
                )
                per_grant = {}  # dedupe duplicate tables within a grant by date
                for tbl in tables:
                    for r in tbl:
                        if len(r) < 4:
                            continue
                        if not re.match(r'\d{1,2}-\w{3}-\d{4}', r[0] or ''):
                            continue
                        if not re.match(r'\d{1,2}-\w{3}-\d{4}', r[-1] or ''):
                            continue  # last col must be the expire/cancel date
                        outstanding = _to_num(r[-2])
                        if outstanding and outstanding > 0:
                            d = datetime.strptime(r[0], "%d-%b-%Y").date().isoformat()
                            per_grant[d] = outstanding
                for d, sh in per_grant.items():
                    shares_by_date[d] = shares_by_date.get(d, 0.0) + sh

            if not shares_by_date:
                if debug:
                    print("[debug] vesting events: no future (outstanding) rows parsed")
                return []

            total_shares = sum(shares_by_date.values())
            price = (unvested_value / total_shares) if total_shares else 0.0
            schedule = [
                {"date": d, "shares": round(shares_by_date[d], 4),
                 "value": round(shares_by_date[d] * price, 2)}
                for d in sorted(shares_by_date)
            ]
            if debug:
                print(f"[debug] vesting events: {len(schedule)} dates, "
                      f"{total_shares:.0f} shares @ ${price:,.2f} = ${total_shares*price:,.2f}")
            # Leave the user's tab back on the dashboard, not a grant popup.
            try:
                page.goto("https://shareworks.solium.com/solium/servlet/ui/dashboard", timeout=20_000)
            except Exception:
                pass
            return schedule
        except Exception as e:
            if debug:
                print(f"[debug] vesting events scrape failed: {e}")
            return []

    def _scrape_vesting_schedule(self, page, vested_baseline, debug):
        """Annual unvested-vesting schedule from the interactive vesting chart.

        The chart ('How your future vestings accumulate over time') exposes
        cumulative portfolio value per year through SVG aria-labels of the form
        'N. YEAR, CUMULATIVE.'. We convert those cumulative points into per-year
        vesting increments above the currently-vested baseline. Annual is the
        finest granularity Shareworks exposes here; returns [] on any failure so
        the dashboard falls back to the summary line.
        """
        chart_url = "https://shareworks.solium.com/solium/servlet/ui/vesting-chart?previous=dashboard"
        try:
            page.goto(chart_url, timeout=30_000)
            page.wait_for_load_state("domcontentloaded", timeout=20_000)
            time.sleep(8)
            labels = page.eval_on_selector_all(
                "[aria-label]",
                "els => els.map(e => e.getAttribute('aria-label') || '')"
                ".filter(t => /\\d{4},\\s*[\\d,]+\\.\\d{2}/.test(t))",
            )
            points = {}
            for t in labels:
                m = re.search(r'(\d{4}),\s*([\d,]+\.\d{2})', t)
                if m:
                    points[int(m.group(1))] = float(m.group(2).replace(",", ""))
            if not points:
                if debug:
                    print("[debug] vesting chart: no cumulative aria-label points found")
                return []
            schedule = []
            prev = vested_baseline
            for year in sorted(points):
                inc = round(points[year] - prev, 2)
                prev = points[year]
                if inc <= 0:
                    continue
                schedule.append({"date": f"{year}-12-31", "shares": None, "value": inc})
            if debug:
                tot = sum(s["value"] for s in schedule)
                print(f"[debug] vesting schedule: {len(schedule)} yearly tranches, sum=${tot:,.2f}")
            return schedule
        except Exception as e:
            if debug:
                print(f"[debug] vesting schedule scrape failed: {e}")
            return []


def _wsl2_host_ip() -> str:
    """Return the Windows host IP from WSL2, or empty string if not on WSL2."""
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                if line.startswith("nameserver"):
                    return line.split()[1].strip()
    except Exception:
        pass
    return ""


def _save_session(ctx):
    cookies = ctx.cookies()
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SESSION_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(cookies, f, indent=2)
    tmp.replace(SESSION_FILE)
    os.chmod(SESSION_FILE, stat.S_IRUSR | stat.S_IWUSR)


def _to_num(s):
    s = (s or "").replace(",", "").strip()
    return float(s) if re.match(r'^-?\d+(\.\d+)?$', s) else None


def _extract_dollar(text: str, labels: list) -> float:
    for label in labels:
        pattern = re.compile(
            re.escape(label) + r'.{0,100}?\$?([\d,]+\.\d{2})',
            re.IGNORECASE | re.DOTALL,
        )
        m = pattern.search(text)
        if m:
            return float(m.group(1).replace(",", ""))
    return 0.0
