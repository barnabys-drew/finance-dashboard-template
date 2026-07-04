"""Loaders for Plaid-synced data (transactions, balances, holdings).

This is the primary data path — when these JSON files exist in PLAID_DIR they
take precedence over the statement parsers in statements.py. Also holds the
Walgreens Rx-fill helpers (the pharmacy/household split is currently unused but
the scraped data is still loaded for the dashboard).
"""

import json
from datetime import datetime

from finance_config import PLAID_DIR, SKIP_PLAID_CATS, _categorize_plaid, load_finance_config


def load_walgreens_fills():
    """Scraped Walgreens Rx fills used to split pharmacy spend from household
    spend at Walgreens. Returns a list of {date, amount, rx?}; [] if the scraper
    hasn't run yet (in which case every Walgreens charge is treated as
    household). See walgreens_pharmacy.py."""
    path = PLAID_DIR / "walgreens_pharmacy.json"
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("fills", []) if isinstance(data, dict) else (data or [])
    except Exception:
        return []


def _is_walgreens_pharmacy(txn, fills, match_days=10):
    """True if a Walgreens bank charge matches a scraped Rx fill. Prefer an exact
    amount match (the copay you paid == the charge, ±$0.01) within match_days of
    the fill date — a charge is usually posted a few days after the fill when the
    Rx is picked up. Fills with $0 paid never match (insurance covered them, so
    no bank charge exists). If fills carry no amount at all, fall back to pure
    date proximity. Unmatched / mixed-basket charges return False → household."""
    if not fills:
        return False
    try:
        tdate = datetime.strptime(txn['date'][:10], "%Y-%m-%d").date()
    except Exception:
        return False
    tamt = round(float(txn['amount']), 2)
    for f in fills:
        fd = f.get('date')
        if not fd:
            continue
        try:
            fdate = datetime.strptime(str(fd)[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if abs((fdate - tdate).days) > match_days:
            continue
        famt = f.get('amount')
        if famt is None:
            return True  # date-proximity match (amount not exposed by scraper)
        if abs(round(float(famt), 2) - tamt) <= 0.01:
            return True
    return False


def load_plaid_transactions():
    path = PLAID_DIR / "plaid_transactions.json"
    if not path.exists():
        return None
    with open(path) as f:
        raw = json.load(f)
    txns = []
    for t in raw:
        # Pending transactions are included (marked in the UI) so the most recent
        # ~1-2 days of Chase activity show up. Amounts may adjust when they post;
        # Plaid's sync removes/updates the pending entry once it settles.
        if t.get("institution") not in ("Chase",):
            continue
        plaid_cat = t.get("plaid_category") or ""
        raw_name_up = (t.get("raw_name") or t.get("description") or "").upper()
        is_atm = "NON-CHASE ATM WITHDRAW" in raw_name_up
        # Outgoing transfers to the Ally HYSA are deliberate savings, not spend.
        is_savings = plaid_cat == "TRANSFER_OUT" and raw_name_up.startswith("ALLY BANK")
        if plaid_cat in SKIP_PLAID_CATS and not is_atm and not is_savings:
            continue
        raw_name = t.get("raw_name", t["description"])
        is_income = t["is_income"]
        if is_income:
            category = "Income"
        elif is_savings:
            category = "Savings"
        elif is_atm:
            category = "Cash / ATM"
        else:
            category = _categorize_plaid(raw_name, plaid_cat)
        txns.append({
            "date":            t["date"],
            "description":     raw_name,
            "amount":          t["amount"],
            "is_income":       is_income,
            "is_savings":      is_savings,
            "category":        category,
            "plaid_category":  plaid_cat,
            "account":         t.get("label", "Chase"),
            "payment_channel": t.get("payment_channel"),
            "pending":         bool(t.get("pending")),
            # Needed so per-charge bucket overrides (assign_bucket / the
            # /finance-recategorize skill) can key off the transaction.
            "transaction_id":  t.get("transaction_id"),
        })
    txns.sort(key=lambda x: x["date"])
    return txns


def load_plaid_holdings():
    path = PLAID_DIR / "plaid_holdings.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


# Friendly display names for raw Plaid account names — set per-user via
# "account_display_names" in config.json (raw name → label).
_PLAID_NAME_OVERRIDES = load_finance_config().get("account_display_names", {})


def load_plaid_networth():
    path = PLAID_DIR / "plaid_balances.json"
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    holdings = load_plaid_holdings()

    # Merge in equity provider balances if present
    provider_path = PLAID_DIR / "provider_balances.json"
    provider_extra = []
    if provider_path.exists():
        with open(provider_path) as f:
            for p in json.load(f):
                if "error" in p:
                    continue
                provider_extra.append({
                    "account_name":    p["account_name"],
                    "account_number":  p["account_name"],
                    "current_value":   p["current_value"],
                    "total_gain_loss": 0.0,
                    "cost_basis":      p["current_value"],
                    "positions": [
                        {
                            "symbol":      pos["symbol"],
                            "description": pos["description"]
                                           + (" (unvested)" if not pos.get("vested", True) else ""),
                            "value":       pos["value"],
                        }
                        for pos in p.get("positions", [])
                    ],
                    "snapshot_date": p.get("as_of", "unknown"),
                    "source":        "Provider",
                    "note":          p.get("note", ""),
                    "vesting_schedule": p.get("vesting_schedule", []),
                })
        if provider_extra:
            # Return Plaid accounts + provider accounts together
            plaid_accounts = _plaid_balance_to_snapshots(data, holdings)
            return plaid_accounts + provider_extra

    return _plaid_balance_to_snapshots(data, holdings)


def _plaid_balance_to_snapshots(data, holdings_data=None):
    holdings_data = holdings_data or {}
    snapshots = []
    for label, info in data.items():
        if "error" in info:
            continue
        institution = info["institution"]
        as_of = info.get("as_of", "unknown")[:10]
        holdings_info = holdings_data.get(label) or {}
        holdings_accounts = {
            acct.get("account_id"): acct
            for acct in holdings_info.get("accounts", [])
            if acct.get("account_id")
        } if "error" not in holdings_info else {}

        cc_liability = 0.0

        for acct in info["accounts"]:
            subtype = acct.get("subtype", "") or ""
            acct_type = acct.get("type", "") or ""
            val = acct.get("current") or 0.0

            # Checking/savings: use the available balance so it matches what the
            # bank app shows (current can lag by pending holds/deposits). Credit
            # cards keep `current` (= amount owed) and investments have no
            # available balance.
            if acct_type == "depository" and acct.get("available") is not None:
                val = acct.get("available")

            # Accumulate CC balances into one liability entry
            if subtype in ("credit card", "paypal"):
                cc_liability += val
                continue

            raw_name = acct.get("name", "") or ""
            acct_name = _PLAID_NAME_OVERRIDES.get(raw_name, raw_name) or label
            holding_acct = holdings_accounts.get(acct.get("account_id"))

            if holding_acct and holding_acct.get("positions"):
                positions = []
                for pos in holding_acct.get("positions", []):
                    positions.append({
                        "symbol":      pos.get("symbol") or "UNKNOWN",
                        "description": pos.get("description") or pos.get("symbol") or "",
                        "value":       round(float(pos.get("value") or 0.0), 2),
                        "quantity":    pos.get("quantity"),
                        "price":       pos.get("price"),
                        "price_as_of": pos.get("price_as_of"),
                        "cost_basis":  pos.get("cost_basis"),
                        "total_gain_loss": pos.get("total_gain_loss"),
                        "security_type": pos.get("security_type"),
                        "security_subtype": pos.get("security_subtype"),
                        "sector": pos.get("sector"),
                        "industry": pos.get("industry"),
                        "is_cash_equivalent": pos.get("is_cash_equivalent"),
                    })

                position_value = sum(p["value"] for p in positions)
                cash_diff = round(val - position_value, 2)
                if cash_diff > 1:
                    positions.append({
                        "symbol": "CASH",
                        "description": "Uninvested cash / sweep",
                        "value": cash_diff,
                        "is_cash_equivalent": True,
                    })

                known_gains = [
                    float(p["total_gain_loss"])
                    for p in positions
                    if p.get("total_gain_loss") is not None
                ]
                gain_loss = round(sum(known_gains), 2) if known_gains else 0.0

                snapshots.append({
                    "account_name":    acct_name,
                    "account_number":  institution,
                    "current_value":   round(val, 2),
                    "total_gain_loss": gain_loss,
                    "cost_basis":      round(val - gain_loss, 2),
                    "positions":       positions,
                    "snapshot_date":   (holdings_info.get("as_of") or as_of)[:10],
                    "source":          "Plaid",
                    "positions_source": "Plaid Investments",
                    "note":            "Holdings refreshed automatically from Plaid Investments",
                })
                continue

            snapshots.append({
                "account_name":    acct_name,
                "account_number":  institution,
                "current_value":   round(val, 2),
                "total_gain_loss": 0.0,
                "cost_basis":      round(val, 2),
                "positions": [{
                    "symbol":      subtype.replace(" ", "_").upper() or "ACCT",
                    "description": acct.get("official_name") or raw_name,
                    "value":       round(val, 2),
                }],
                "snapshot_date": as_of,
                "source":        "Plaid",
            })

        if cc_liability > 0:
            snapshots.append({
                "account_name":    "CC Liability",
                "account_number":  institution,
                "current_value":   round(-cc_liability, 2),
                "total_gain_loss": 0.0,
                "cost_basis":      round(-cc_liability, 2),
                "positions": [{
                    "symbol":      "CC_DEBT",
                    "description": "Credit Card Balance",
                    "value":       round(-cc_liability, 2),
                }],
                "snapshot_date": as_of,
                "source":        "Plaid",
            })

    return snapshots
