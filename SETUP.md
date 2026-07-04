# Setup Guide

This guide takes you from a fresh clone to a dashboard showing your real
accounts. Every step is explicit and copy-pasteable — you can follow it
yourself, or paste it into an AI coding assistant and let it drive.

**Path of least resistance:** run the demo first (Step 1), connect Plaid
(Steps 2–4), and only then bother with the optional scrapers (Step 6).

---

## 1. Install and smoke-test

Requirements: Python 3.10+, Linux / macOS / WSL2.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Optional: build the CSS (a committed build is included, so this can wait)
./scripts/setup-tailwind.sh
./bin/tailwindcss -i static/src/input.css -o static/app.css --minify

# Smoke test with generated sample data — no credentials needed
./start.sh --demo
```

You should see the full dashboard at `http://localhost:5000`. Ctrl-C stops it.

## 2. Get Plaid credentials

Plaid is the industry-standard bank aggregator — it handles OAuth and 2FA so
your bank credentials never touch this code.

1. Create a free account at <https://dashboard.plaid.com/signup>.
2. Request **Development** access (free tier: up to 100 linked items) or start in Sandbox.
3. From *Team Settings → Keys*, note your **client_id** and **secret**.

## 3. Create your local config

All personal data lives in `~/.finance-viz/` — never in the repo.

```bash
mkdir -p ~/.finance-viz
cp config.example.json ~/.finance-viz/config.json
```

Edit `~/.finance-viz/config.json` and set `plaid_client_id`.

The Plaid **secret** goes in its own file (preferred), or 1Password:

```bash
# Option A — plain file:
echo -n "YOUR_PLAID_SECRET" > ~/.finance-viz/.plaid_secret
chmod 600 ~/.finance-viz/.plaid_secret

# Option B — 1Password CLI (op): store the secret in an item and set
# plaid_secret_1password_item / plaid_secret_1password_field in config.json.
```

## 4. Link your bank accounts

```bash
source venv/bin/activate
python3 plaid_setup.py
```

This opens Plaid Link in a browser for each institution (Chase, Fidelity,
Ally, … whatever you use) and writes access tokens to
`~/.finance-viz/plaid_tokens.json` (chmod 600). Re-run it any time to add
another institution.

## 5. Run it for real

```bash
./start.sh
```

That refreshes Plaid data, starts the dashboard, and opens it in your browser.
From now on this is the only command you need. See `RUN.md` for the other
flags (`--fast`, `--no-shareworks`, `--demo`).

## 6. Optional: scrape what Plaid can't reach

Some balances aren't in Plaid — equity plans (Shareworks/Morgan Stanley),
pharmacy spend, etc. The pattern used here: drive **your own logged-in Chrome**
over the remote-debugging protocol (CDP, port 9222) with Playwright. No
credentials in code; you log in manually once and the session persists.

```bash
pip install playwright playwright-stealth
```

### Shareworks (employer RSUs)

Add to `"providers"` in `~/.finance-viz/config.json`:

```json
{
  "type": "shareworks",
  "1password_item": "Shareworks",
  "url": "https://shareworks.solium.com/solium/servlet/userLogin.do",
  "symbol": "YOUR_COMPANY_TICKER"
}
```

Also set `"employer_symbols": ["YOUR_TICKER"]` and
`"employer_keywords": ["your company name"]` so the concentration analytics
classify it as employer equity.

`./start.sh` will launch/reuse Chrome on the debug port and scrape vested and
unvested RSUs plus the vesting schedule. First run: log in when the Chrome
window opens, then re-run. On WSL the Chrome paths are auto-derived; override
`CHROME_EXE` / `USER_DATA` / `PROFILE` in `~/.finance-viz/start.env` if needed.

### Writing your own provider

Subclass `BaseProvider` in `providers/`, implement `fetch()` returning
`{account_name, current_value, positions, as_of}` (see `providers/base.py`
for the full shape and `providers/manual.py` for the simplest example), then
register it in `providers/__init__.py` and reference its `type` in config.
`refresh.py --providers` picks it up automatically.

### Walgreens pharmacy split (US)

Set `"walgreens": {"enabled": true}` to scrape Rx fill history over the same
Chrome session, so pharmacy copays split out of front-store Walgreens spend.

## 7. Make the categories yours

Everything is keyword-driven from `config.json` — no code changes:

| Key | What it does |
|-----|--------------|
| `categories` | Ordered `{Category: [keywords]}` rules for the Spending tab (first match wins; falls back to Plaid's category) |
| `household_keywords`, `baby_keywords`, `pet_keywords`, `health_keywords`, `transport_keywords`, `ai_keywords` | Route merchants into the insight tiles |
| `utility_keywords` | Your utility providers — peeled off before rent |
| `budgets` | Monthly budget per tile, e.g. `{"food": 400}` |
| `bucket_overrides` | Per-transaction tile override: `{"<transaction_id>": "household"}` |
| `merchant_aliases` | Display names: `{"OAKVIEW": "Oakview Properties (Rent)"}` |
| `account_display_names` | Rename raw Plaid account names in the UI |
| `concentration.thresholds` | Net-worth concentration alert levels |

Run `python3 refresh.py --dry-run` to sanity-check without writing anything.

## Troubleshooting

- **Port 5000 busy** — `start.sh` kills the old server automatically; if
  something else owns it: `lsof -ti:5000 | xargs kill`.
- **Plaid "secret" error** — regenerate `~/.finance-viz/.plaid_secret` (Step 3).
- **Chrome won't bind port 9222** — every Chrome process must exit before the
  debug port can bind; `start.sh` handles this, but see `RUN.md` for the details.
- **Tests** — `pip install -r requirements-dev.txt && python3 -m pytest -q`
  (34 tests, no config or network needed).
