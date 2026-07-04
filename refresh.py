"""
Main refresh script — pulls live data from all sources and writes to ~/.finance-viz/.

Sources:
  1. Plaid  — bank accounts, brokerages (Chase, Fidelity, Ally, Principal)
  2. Providers — anything Plaid can't reach (Shareworks, manual entries, etc.)
     Configured via the "providers" list in ~/.finance-viz/config.json.
  3. Position news — Yahoo Finance RSS for the current holdings.

Usage:
    python3 refresh.py             # full refresh
    python3 refresh.py --dry-run   # print results, write nothing
    python3 refresh.py --plaid     # Plaid only
    python3 refresh.py --providers # equity providers only

Output files (in ~/.finance-viz/):
    plaid_balances.json      — bank/brokerage balances
    plaid_transactions.json  — transaction history
    plaid_cursors.json       — incremental sync state (don't delete)
    provider_balances.json   — equity/manual provider balances
    position_news.json       — news tied to current holdings
    refresh.log              — full run history
"""

import json, os, stat, sys, logging
from datetime import datetime
from pathlib import Path

BASE_DIR      = Path.home() / ".finance-viz"
CONFIG_FILE   = BASE_DIR / "config.json"
PROVIDER_FILE = BASE_DIR / "provider_balances.json"
LOG_FILE      = BASE_DIR / "refresh.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

DRY_RUN        = "--dry-run"   in sys.argv
# --plaid / --providers / --walgreens scope the run to just that source; with
# none of them, everything runs. News follows its own --no-news flag (unchanged).
_SCOPED        = any(f in sys.argv for f in ("--plaid", "--providers", "--walgreens"))
RUN_PLAID      = "--plaid"     in sys.argv or not _SCOPED
RUN_PROVIDERS  = "--providers" in sys.argv or not _SCOPED
RUN_WALGREENS  = "--walgreens" in sys.argv or not _SCOPED
RUN_NEWS       = "--no-news"   not in sys.argv


def save_json(path, data):
    if DRY_RUN:
        return
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    tmp.replace(path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def run_plaid():
    import plaid_refresh
    plaid_refresh.DRY_RUN = DRY_RUN
    plaid_refresh.main()


def run_providers():
    with open(CONFIG_FILE) as f:
        cfg = json.load(f)

    from providers import load_providers
    from providers.base import ProviderError

    provider_list = load_providers(cfg)
    if not provider_list:
        log.info("No providers configured in config.json — skipping")
        return

    results, failed = [], False
    for p in provider_list:
        log.info("Running provider: %s", p.name)
        try:
            data = p.fetch()
            results.append(data)
            log.info("  %s: $%.2f (as of %s)%s",
                     data["account_name"],
                     data["current_value"],
                     data.get("as_of", "?"),
                     f" — NOTE: {data['note']}" if data.get("note") else "")
        except ProviderError as e:
            log.error("  Provider '%s' failed: %s", p.name, e)
            failed = True
        except Exception as e:
            log.error("  Provider '%s' unexpected error: %s", p.name, e)
            failed = True

    # A failed provider must actually KEEP its cached balance: carry over any
    # previous non-error entry that no fresh result replaces. Without this, a
    # failed scrape overwrote the file with an error stub and the account
    # vanished from Net Worth until the next successful refresh.
    if failed and PROVIDER_FILE.exists():
        try:
            with open(PROVIDER_FILE) as f:
                prev = json.load(f)
            fresh_names = {r.get("account_name") for r in results}
            carried = [r for r in prev
                       if not r.get("error") and r.get("account_name") not in fresh_names]
            if carried:
                log.info("Keeping cached data for %d provider account(s) after failure: %s",
                         len(carried), ", ".join(r["account_name"] for r in carried))
                results.extend(carried)
        except Exception as e:
            log.error("Could not carry over cached provider data: %s", e)

    if DRY_RUN:
        print("\n--- PROVIDER BALANCES ---")
        for r in results:
            if "error" in r:
                print(f"  {r['account_name']}: ERROR — {r['error']}")
            else:
                print(f"  {r['account_name']}: ${r['current_value']:,.2f}")
                for pos in r.get("positions", []):
                    vested = "vested" if pos.get("vested") else "unvested"
                    print(f"    {pos['symbol']}: ${pos['value']:,.2f}  {pos['description']} ({vested})")
    else:
        save_json(PROVIDER_FILE, results)
        log.info("Wrote %s", PROVIDER_FILE)


def run_walgreens():
    """Scrape Walgreens Rx fill history (date + copay paid) so the dashboard can
    split pharmacy spend from front-store spend at Walgreens. Gated by a config
    toggle; tolerant of an expired session (logs and keeps cached data)."""
    with open(CONFIG_FILE) as f:
        cfg = json.load(f)
    if not cfg.get("walgreens", {}).get("enabled", False):
        log.info("Walgreens scrape disabled in config — skipping")
        return

    import walgreens_pharmacy
    try:
        data = walgreens_pharmacy.scrape(debug=False)
    except Exception as e:
        log.error("  Walgreens refresh failed/expired — using cached data: %s", e)
        return

    fills = data.get("fills", [])
    paid = [f for f in fills if f.get("amount", 0) > 0]
    log.info("  Walgreens — %d fills (%d copays, $%.2f)",
             len(fills), len(paid), sum(f["amount"] for f in paid))
    if DRY_RUN:
        print("\n--- WALGREENS FILLS ---")
        for f in fills:
            print(f"  {f['date']}  ${f['amount']:>7.2f}  {f['rx']}")
    else:
        save_json(walgreens_pharmacy.OUTPUT_FILE, data)
        log.info("Wrote %s", walgreens_pharmacy.OUTPUT_FILE)


def run_news():
    from position_news import refresh_position_news

    try:
        data = refresh_position_news(write=not DRY_RUN)
        log.info("Position news: %d stories for %d symbols",
                 len(data.get("stories", [])), len(data.get("symbols", [])))
        if data.get("errors"):
            log.info("Position news skipped/failed for %d symbols", len(data["errors"]))
        if DRY_RUN:
            print("\n--- POSITION NEWS ---")
            for story in data.get("stories", [])[:10]:
                print(f"  {story['symbol']}: {story['title']}")
    except Exception as e:
        log.error("Position news refresh failed: %s", e)


def main():
    log.info("=== Refresh %s %s ===",
             "DRY-RUN" if DRY_RUN else "",
             datetime.now().strftime("%Y-%m-%d %H:%M"))

    if RUN_PLAID:
        log.info("-- Plaid --")
        run_plaid()

    if RUN_PROVIDERS:
        log.info("-- Equity providers --")
        run_providers()

    if RUN_WALGREENS:
        log.info("-- Walgreens Rx --")
        run_walgreens()

    if RUN_NEWS:
        log.info("-- Position news --")
        run_news()

    log.info("=== Done ===")


if __name__ == "__main__":
    main()
