"""
Pull live balances and transactions from Plaid and write to ~/.finance-viz/.
Run manually or via cron — dashboard reads the output files on every page load.

Usage:
    python3 plaid_refresh.py           # full refresh
    python3 plaid_refresh.py --dry-run # print results, don't write files
"""

import json, sys, os, stat, logging
import urllib.error
import urllib.request
from datetime import datetime, date, timedelta
from pathlib import Path

import plaid
from plaid.api import plaid_api
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest

BASE_DIR     = Path.home() / ".finance-viz"
TOKEN_FILE   = BASE_DIR / "plaid_tokens.json"
CONFIG_FILE  = BASE_DIR / "config.json"
BALANCE_FILE = BASE_DIR / "plaid_balances.json"
TXN_FILE     = BASE_DIR / "plaid_transactions.json"
CURSOR_FILE  = BASE_DIR / "plaid_cursors.json"
HOLDINGS_FILE = BASE_DIR / "plaid_holdings.json"
LOG_FILE     = BASE_DIR / "refresh.log"

# First run on a fresh machine: the data dir doesn't exist yet, and the
# FileHandler below would crash at import time without it.
BASE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

DRY_RUN = "--dry-run" in sys.argv

# Chase token label — this account gets full transaction sync
CHASE_LABEL = "Chase Checking"

# How many days of transactions to fetch on first run (no cursor yet)
INITIAL_DAYS = 180

INVESTMENT_ACCOUNT_TYPES = {"investment"}
INVESTMENT_ACCOUNT_SUBTYPES = {
    "401a", "401k", "403B", "457b", "529", "brokerage", "cash isa", "education savings account",
    "fixed annuity", "gic", "health reimbursement arrangement", "hsa", "ira", "isa",
    "keogh", "lif", "life insurance", "lira", "lrif", "lrsp", "mutual fund", "non-custodial wallet",
    "non-taxable brokerage account", "other", "other insurance", "other annuity", "prif",
    "rdsp", "resp", "retirement", "rlif", "roth", "roth 401k", "rrif", "rrsp", "sarsep",
    "sep ira", "simple ira", "sipp", "stock plan", "tfsa", "thrift savings plan", "trust",
    "ugma", "utma", "variable annuity",
}


SECRET_CACHE = Path.home() / ".finance-viz" / ".plaid_secret"
PLAID_API_BASE = "https://production.plaid.com"


def get_plaid_secret(cfg: dict) -> str:
    """Return Plaid secret. Priority: cached file → 1Password → config value."""
    if SECRET_CACHE.exists():
        secret = SECRET_CACHE.read_text().strip()
        if secret:
            return secret

    op_item  = cfg.get("plaid_secret_1password_item")
    op_field = cfg.get("plaid_secret_1password_field", "secret")
    if op_item:
        import subprocess
        try:
            result = subprocess.run(
                ["op", "item", "get", op_item, "--field", op_field],
                capture_output=True, text=True, check=True,
            )
            secret = result.stdout.strip()
            if secret:
                SECRET_CACHE.write_text(secret)
                SECRET_CACHE.chmod(0o600)
                log.info("Cached Plaid secret to %s", SECRET_CACHE)
                return secret
        except Exception as e:
            log.warning("1Password fetch failed (%s)", e)

    return cfg.get("plaid_secret", "")


def get_client():
    with open(CONFIG_FILE) as f:
        cfg = json.load(f)
    secret = get_plaid_secret(cfg)
    configuration = plaid.Configuration(
        host=plaid.Environment.Production,
        api_key={"clientId": cfg["plaid_client_id"], "secret": secret},
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


def load_tokens():
    with open(TOKEN_FILE) as f:
        return json.load(f)


def load_cursors():
    if CURSOR_FILE.exists():
        with open(CURSOR_FILE) as f:
            return json.load(f)
    return {}


def save_json(path, data):
    if DRY_RUN:
        return
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    tmp.replace(path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _as_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _model_date(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _has_investment_accounts(balance_data):
    for acct in balance_data.get("accounts", []):
        acct_type = (acct.get("type") or "").lower()
        subtype = (acct.get("subtype") or "").lower()
        if acct_type in INVESTMENT_ACCOUNT_TYPES or subtype in INVESTMENT_ACCOUNT_SUBTYPES:
            return True
    return False


def _post_plaid_json(endpoint, payload):
    """Call a Plaid endpoint directly.

    The generated SDK can fail response-model validation for investments payloads
    even when Plaid returns valid JSON. Using the raw endpoint keeps refreshes
    working while the rest of the app still uses the SDK for balances/txns.
    """
    req = urllib.request.Request(
        f"{PLAID_API_BASE}{endpoint}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Plaid-Version": "2020-09-14",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Plaid {endpoint} failed with HTTP {e.code}: {body}") from e


def refresh_balances(client, tokens):
    balances = {}
    # Previous snapshot so a transient Plaid failure (e.g. a 500 on one
    # institution) keeps that institution's cached balances instead of
    # replacing them with an error stub — which made the accounts vanish
    # from Net Worth until the next successful refresh.
    prev = {}
    if BALANCE_FILE.exists():
        try:
            with open(BALANCE_FILE) as f:
                prev = json.load(f)
        except Exception:
            prev = {}
    for label, token_data in tokens.items():
        access_token = token_data["access_token"]
        institution  = token_data["institution"]
        try:
            resp = client.accounts_balance_get(
                AccountsBalanceGetRequest(access_token=access_token)
            )
            accounts = []
            for acct in resp.accounts:
                bal = acct.balances
                accounts.append({
                    "account_id":   acct.account_id,
                    "name":         acct.name,
                    "official_name": acct.official_name,
                    "type":         str(acct.type),
                    "subtype":      str(acct.subtype),
                    "current":      bal.current,
                    "available":    bal.available,
                    "limit":        bal.limit,
                    "iso_currency": bal.iso_currency_code,
                })
            balances[label] = {
                "institution": institution,
                "accounts":    accounts,
                "as_of":       datetime.now().isoformat(),
            }
            total = sum(a["current"] or 0 for a in accounts)
            log.info("%-20s %d accounts  total=$%.2f", label, len(accounts), total)
        except Exception as e:
            log.error("balance fetch failed for %s: %s", label, e)
            if label in prev and "error" not in prev[label]:
                log.info("%-20s keeping cached balances (as of %s)",
                         label, prev[label].get("as_of", "?"))
                balances[label] = prev[label]
            else:
                balances[label] = {"institution": institution, "error": str(e)}
    return balances


def refresh_transactions(client, tokens, cursors):
    """Incremental sync for all tokens. Merges delta into existing stored transactions."""
    # Load existing transactions keyed by transaction_id for fast merge
    existing: dict[str, dict] = {}
    if TXN_FILE.exists():
        with open(TXN_FILE) as f:
            for t in json.load(f):
                existing[t["transaction_id"]] = t

    new_cursors = dict(cursors)

    for label, token_data in tokens.items():
        access_token = token_data["access_token"]
        cursor = cursors.get(label)

        added, modified, removed = [], [], []
        has_more = True

        try:
            while has_more:
                kwargs = {"access_token": access_token}
                if cursor:
                    kwargs["cursor"] = cursor
                resp = client.transactions_sync(
                    TransactionsSyncRequest(**kwargs)
                )
                added    += resp.added
                modified += resp.modified
                removed  += [r.transaction_id for r in resp.removed]
                has_more  = resp.has_more
                cursor    = resp.next_cursor

            new_cursors[label] = cursor
            log.info("%-20s +%d txns  modified=%d  removed=%d",
                     label, len(added), len(modified), len(removed))

            for t in added + modified:
                amt = t.amount  # Plaid: positive = debit (money out), negative = credit
                existing[t.transaction_id] = {
                    "date":        str(t.date),
                    "description": t.merchant_name or t.name,
                    "raw_name":    t.name,
                    "amount":      abs(float(amt)),
                    "is_income":   amt < 0,  # negative amount = money coming in
                    "plaid_category": (t.personal_finance_category.primary
                                       if t.personal_finance_category else None),
                    "payment_channel": t.payment_channel,
                    "account_id":  t.account_id,
                    "institution": token_data["institution"],
                    "label":       label,
                    "transaction_id": t.transaction_id,
                    "pending":     t.pending,
                }

            for tid in removed:
                existing.pop(tid, None)

        except Exception as e:
            log.error("transaction sync failed for %s: %s", label, e)

    all_txns = sorted(existing.values(), key=lambda t: t["date"], reverse=True)
    return all_txns, new_cursors


def refresh_holdings(tokens, balances, cfg, secret):
    """Fetch investment holdings for Plaid items that appear to contain investments."""
    results = {}
    prev = {}
    if HOLDINGS_FILE.exists():
        try:
            with open(HOLDINGS_FILE) as f:
                prev = json.load(f)
        except Exception:
            prev = {}

    for label, token_data in tokens.items():
        balance_data = balances.get(label, {})
        if "error" in balance_data:
            # Same cache-preserving rule as balances: a failed institution
            # keeps its last good holdings rather than dropping them.
            if label in prev:
                log.info("%-20s keeping cached holdings", label)
                results[label] = prev[label]
            continue
        if not _has_investment_accounts(balance_data):
            continue

        access_token = token_data["access_token"]
        institution = token_data["institution"]

        try:
            resp = _post_plaid_json("/investments/holdings/get", {
                "client_id": cfg["plaid_client_id"],
                "secret": secret,
                "access_token": access_token,
            })

            accounts_by_id = {}
            for acct in resp.get("accounts", []):
                bal = acct.get("balances") or {}
                account_id = acct.get("account_id")
                accounts_by_id[account_id] = {
                    "account_id": account_id,
                    "name": acct.get("name"),
                    "official_name": acct.get("official_name"),
                    "type": str(acct.get("type") or ""),
                    "subtype": str(acct.get("subtype") or ""),
                    "current": _as_float(bal.get("current")),
                    "available": bal.get("available"),
                    "limit": bal.get("limit"),
                    "iso_currency": bal.get("iso_currency_code"),
                    "positions": [],
                }

            securities_by_id = {s.get("security_id"): s for s in resp.get("securities", [])}
            for holding in resp.get("holdings", []):
                acct = accounts_by_id.get(holding.get("account_id"))
                if not acct:
                    continue
                sec = securities_by_id.get(holding.get("security_id")) or {}
                symbol = (sec.get("ticker_symbol") or "").replace("*", "").strip()
                name = sec.get("name") or ""
                if not symbol:
                    if sec.get("is_cash_equivalent"):
                        symbol = "CASH"
                    elif name:
                        symbol = name[:18].upper().replace(" ", "_")
                    else:
                        symbol = "UNKNOWN"

                value = _as_float(holding.get("institution_value"))
                cost_basis = holding.get("cost_basis")
                total_gain_loss = None
                if cost_basis is not None:
                    total_gain_loss = round(value - _as_float(cost_basis), 2)

                acct["positions"].append({
                    "symbol": symbol,
                    "description": name or symbol,
                    "value": round(value, 2),
                    "quantity": _as_float(holding.get("quantity")),
                    "price": _as_float(holding.get("institution_price")),
                    "price_as_of": _model_date(holding.get("institution_price_as_of") or holding.get("institution_price_datetime")),
                    "cost_basis": round(_as_float(cost_basis), 2) if cost_basis is not None else None,
                    "total_gain_loss": total_gain_loss,
                    "security_type": str(sec.get("type") or ""),
                    "security_subtype": str(sec.get("subtype") or ""),
                    "sector": sec.get("sector"),
                    "industry": sec.get("industry"),
                    "is_cash_equivalent": bool(sec.get("is_cash_equivalent")),
                })

            account_list = list(accounts_by_id.values())
            for acct in account_list:
                acct["positions"].sort(key=lambda p: abs(p["value"]), reverse=True)
                positions_value = sum(_as_float(p["value"]) for p in acct["positions"])
                if positions_value:
                    acct["holdings_value"] = round(positions_value, 2)

            results[label] = {
                "institution": institution,
                "accounts": account_list,
                "securities_count": len(resp.get("securities", [])),
                "holdings_count": len(resp.get("holdings", [])),
                "is_investments_fallback_item": bool(resp.get("is_investments_fallback_item")),
                "as_of": datetime.now().isoformat(),
            }
            log.info("%-20s %d holdings across %d investment accounts",
                     label, len(resp.get("holdings", [])), len(account_list))
        except Exception as e:
            msg = str(e)
            log.error("holdings fetch failed for %s: %s", label, msg)
            results[label] = {"institution": institution, "error": msg, "as_of": datetime.now().isoformat()}

    return results


def main():
    log.info("=== Plaid refresh %s ===", "DRY-RUN" if DRY_RUN else "")

    with open(CONFIG_FILE) as f:
        cfg = json.load(f)
    secret = get_plaid_secret(cfg)
    if not secret:
        log.error(
            "No Plaid API secret available (1Password not signed in and no plaid_secret "
            "fallback in config.json). Keeping existing cached data — sign into 1Password "
            "and retry."
        )
        return

    client  = get_client()
    tokens  = load_tokens()
    cursors = load_cursors()

    balances = refresh_balances(client, tokens)
    txns, new_cursors = refresh_transactions(client, tokens, cursors)
    holdings = refresh_holdings(tokens, balances, cfg, secret)

    if not DRY_RUN:
        save_json(BALANCE_FILE, balances)
        save_json(TXN_FILE, txns)
        save_json(CURSOR_FILE, new_cursors)
        save_json(HOLDINGS_FILE, holdings)
        log.info("Wrote %s", BALANCE_FILE)
        log.info("Wrote %s (%d transactions)", TXN_FILE, len(txns))
        log.info("Wrote %s", HOLDINGS_FILE)
    else:
        print("\n--- BALANCES ---")
        for label, data in balances.items():
            if "error" in data:
                print(f"  {label}: ERROR — {data['error']}")
            else:
                for a in data["accounts"]:
                    print(f"  {label} / {a['name']}: ${a['current']:.2f}")
        print(f"\n--- TRANSACTIONS ({len(txns)} total) ---")
        for t in txns[:10]:
            direction = "IN " if t["is_income"] else "OUT"
            print(f"  {t['date']} {direction} ${t['amount']:7.2f}  {t['description']}")
        if len(txns) > 10:
            print(f"  ... and {len(txns)-10} more")
        print("\n--- HOLDINGS ---")
        for label, data in holdings.items():
            if "error" in data:
                print(f"  {label}: ERROR — {data['error']}")
                continue
            print(f"  {label}: {data['holdings_count']} holdings")
            for acct in data["accounts"]:
                print(f"    {acct['name']}: ${acct.get('current', 0):,.2f}")
                for p in acct.get("positions", [])[:8]:
                    print(f"      {p['symbol']:<8} ${p['value']:>10,.2f}  {p['description']}")

    log.info("Refresh complete.")


if __name__ == "__main__":
    main()
