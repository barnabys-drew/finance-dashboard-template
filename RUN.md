# Running the dashboard

One command brings up the dashboard in the right Chrome profile with fresh data:

```bash
./start.sh
```

That's it. The page opens in your **Default Chrome profile** — the one logged
into Shareworks — at <http://localhost:5000>.

## Modes

| Command | What it does |
|---------|--------------|
| `./start.sh` | Refresh Plaid + Shareworks, then open the dashboard. Default. |
| `./start.sh --fast` | Skip all refresh — open cached data **instantly**. Use when you just want the page up now. |
| `./start.sh --no-shareworks` | Refresh Plaid only; don't touch Chrome. Fast, no window disruption. |
| `./start.sh --demo` | Generated sample data — no credentials, Plaid, or Chrome. Try the dashboard in one command. |

Stop the server with `Ctrl-C` (the script cleans it up for you).

## What the script does, in order

1. Stops any server already on port 5000.
2. Starts the Flask server in the background (it serves cached data immediately,
   so boot overlaps with the data refresh below).
3. **Plaid** — refreshes Chase, Fidelity, Ally, Principal (secret read from
   `~/.finance-viz/.plaid_secret`, no 1Password prompt).
4. **Shareworks** — ensures Chrome is on the Default profile + debug port 9222,
   then scrapes vested/unvested RSUs from your equity plan.
5. **Walgreens Rx** — reusing that same Chrome, scrapes the Prescription Records
   report (`/rx-settings/print-rx`) for fill dates + copay amounts, writing
   `~/.finance-viz/walgreens_pharmacy.json`. The dashboard uses it to split
   Walgreens bank charges: a charge whose amount matches a copay (within ~10
   days of the fill) counts as **Health/pharmacy**; everything else at Walgreens
   is **Household**. Toggle via the `"walgreens"` block in `config.json`
   (`enabled`, `match_days`). Disabled or not-logged-in → all Walgreens stays
   Household.
6. Waits for the server to answer, then opens `localhost:5000` as a new tab in
   that same Default-profile Chrome.

## The Chrome / Shareworks detail

Shareworks has no API, so it's scraped from a real logged-in Chrome over the
remote-debugging protocol (CDP, port 9222). Two cases:

- **Session already live** (port 9222 up): the script reuses it — nothing is
  disrupted, no relaunch, no login. This is the common, fast path because the
  scraper deliberately leaves Chrome open so the session persists between runs.
- **Port down**: the script force-quits **all** Chrome (the debug port only
  binds when no other Chrome owns the Default profile), relaunches Chrome on the
  Default profile pointed at the Shareworks dashboard, and waits for the port.
  ⚠️ This closes your open Chrome windows.

If the Shareworks session has **expired**, the scrape fails and the script falls
back to cached Shareworks data and prints a note. To fix: log into Shareworks in
the Chrome window that's now open (keep "Remember my account" checked, finish
2FA, land on the dashboard), then rerun `./start.sh`. Once logged in, the
session persists and future runs hit the fast path automatically.

**Walgreens** works the same way over the same Chrome: if its session expires
the scrape logs a note and the dashboard keeps the last `walgreens_pharmacy.json`
(or, with no file, treats all Walgreens as Household). To refresh, log into
walgreens.com (Pharmacy) in that Chrome and rerun — or just
`python3 refresh.py --walgreens`. Credentials live in 1Password; login is always
manual (walgreens.com has bot detection).

## Why the Default profile (not a throwaway one)

A dedicated `--user-data-dir` binds the debug port reliably but is a blank
profile with no Shareworks login, so it never authenticates. The Default profile
carries the login but the debug port only binds when no other Chrome owns it —
hence the force-kill. Trade-off: a fresh login costs you all open Chrome
windows; a live session costs nothing.

## Process control from WSL

All Chrome process control uses **PowerShell**, not `cmd` `taskkill`/`tasklist`
— from WSL those give a UNC-path warning and unreliable output, and a plain
`cmd start` launch hits "Opening in existing browser session" without ever
binding port 9222.

## Styling (Tailwind)

The UI is styled with Tailwind via the **standalone CLI** (no node_modules).

- One-time setup after cloning: `./scripts/setup-tailwind.sh` (downloads `bin/tailwindcss`, gitignored).
- `start.sh` rebuilds `static/app.css` on launch if the CLI is present; otherwise it
  falls back to the committed `static/app.css`, so the app always runs.
- Manual build: `./bin/tailwindcss -i static/src/input.css -o static/app.css --minify`
- Live dev watch while editing styles: `./bin/tailwindcss -i static/src/input.css -o static/app.css --watch`
- Tokens (colors/radius/font) live in `tailwind.config.js`; reusable CSS in `static/src/input.css`.

## Paths

- Chrome: `C:\Program Files\Google\Chrome\Application\chrome.exe`
- User Data: derived from Windows `%LOCALAPPDATA%` (`...\Google\Chrome\User Data`), profile `Default`
- Server log: `/tmp/finance-viz-server.log`
- Data + secrets: `~/.finance-viz/` (see README.md)
- Machine overrides: `~/.finance-viz/start.env` (optional; sourced by `start.sh` —
  set `CHROME_EXE`, `USER_DATA`, `PROFILE`, `export FINANCE_VIZ_PDF_DIR=...`, etc.)
