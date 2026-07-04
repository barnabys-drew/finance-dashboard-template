"""Generate fake but realistic dashboard data for screenshots / demos.

Writes the same JSON files the Plaid refresh would produce into a target dir
(default: a fresh temp dir), so the app can be run against demo data without
touching the real ~/.finance-viz. Nothing here is real — names and amounts are
invented.

Usage:
    python3 scripts/gen_sample_data.py [OUTPUT_DIR]
"""

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

random.seed(42)  # deterministic output so screenshots are reproducible

# (raw_name, plaid_category, low, high) — recurring/typical merchants by category.
MERCHANTS = [
    ("Whole Foods Market",      "GROCERIES",            55, 180),
    ("Costco Wholesale",        "GENERAL_MERCHANDISE",  90, 320),
    ("Trader Joe's",            "GROCERIES",            30, 95),
    ("DoorDash",                "GENERAL_MERCHANDISE",  22, 65),
    ("Olive Garden",  "FOOD_AND_DRINK",       28, 70),
    ("Chipotle",                "FOOD_AND_DRINK",       14, 38),
    ("Starbucks",               "FOOD_AND_DRINK",       5, 16),
    ("Anthropic",               "GENERAL_SERVICES",     10, 45),
    ("Claude.ai",               "GENERAL_SERVICES",     20, 60),
    ("Cursor Usage",            "GENERAL_SERVICES",     8, 40),
    ("Netflix",                 "ENTERTAINMENT",        15.99, 15.99),
    ("Spotify",                 "ENTERTAINMENT",        11.99, 11.99),
    ("Duke Energy",                   "RENT_AND_UTILITIES",   60, 160),
    ("Verizon Wireless",        "RENT_AND_UTILITIES",   95, 95),
    ("City Transit Authority",              "TRANSPORTATION",       8, 28),
    ("Shell Gas",               "TRANSPORTATION",       35, 70),
    ("Target",                  "GENERAL_MERCHANDISE",  25, 140),
    ("Nike",                    "GENERAL_MERCHANDISE",  40, 160),
    ("Walgreens Pharmacy",      "MEDICAL",              12, 65),
    ("PetSmart",                "GENERAL_MERCHANDISE",  35, 110),
    ("State Farm Insurance",    "INSURANCE",            142, 142),
]


def _txn(d, raw, cat, amt, **extra):
    t = {
        "date": d.isoformat(),
        "description": raw,
        "raw_name": raw,
        "amount": round(amt, 2),
        "is_income": False,
        "institution": "Chase",
        "plaid_category": cat,
        "label": "Chase Checking",
        "payment_channel": random.choice(["online", "in store"]),
        "pending": False,
    }
    t.update(extra)
    return t


def build_transactions():
    txns = []
    start = date(2026, 3, 1)
    end = date(2026, 6, 27)

    # Twice-monthly paycheck + monthly rent + monthly savings transfer.
    d = start
    while d <= end:
        if d.day in (1, 15):
            txns.append(_txn(d, "ACME CORP PAYROLL", "INCOME", 3120.00,
                             is_income=True, plaid_category="INCOME"))
        if d.day == 1:
            txns.append(_txn(d, "OAKVIEW PROPERTIES RENT", "RENT_AND_UTILITIES", 2150.00))
            txns.append(_txn(d, "ALLY BANK TRANSFER", "TRANSFER_OUT", 600.00,
                             is_savings=True, plaid_category="TRANSFER_OUT"))
        d += timedelta(days=1)

    # Scatter discretionary spend across the period.
    day = start
    while day <= end:
        for raw, cat, lo, hi in MERCHANTS:
            # Each merchant hits a few random days per month.
            if random.random() < 0.07:
                amt = lo if lo == hi else random.uniform(lo, hi)
                txns.append(_txn(day, raw, cat, amt))
        day += timedelta(days=1)

    txns.sort(key=lambda t: t["date"])
    return txns


def build_balances():
    return {
        "Chase": {
            "institution": "Chase",
            "as_of": "2026-06-27T12:00:00",
            "accounts": [
                {"account_id": "chk1", "name": "TOTAL CHECKING", "type": "depository",
                 "subtype": "checking", "current": 9240.55, "available": 9240.55,
                 "official_name": "Chase Total Checking"},
                {"account_id": "cc1", "name": "Chase Sapphire", "type": "credit",
                 "subtype": "credit card", "current": 1685.20},
            ],
        },
        "Ally": {
            "institution": "Ally",
            "as_of": "2026-06-27T12:00:00",
            "accounts": [
                {"account_id": "sav1", "name": "Savings Account", "type": "depository",
                 "subtype": "savings", "current": 28500.00, "available": 28500.00},
            ],
        },
        "Fidelity": {
            "institution": "Fidelity",
            "as_of": "2026-06-27T12:00:00",
            "accounts": [
                {"account_id": "roth1", "name": "ROTH IRA", "type": "investment",
                 "subtype": "roth", "current": 46820.00},
            ],
        },
    }


def build_holdings():
    return {
        "Fidelity": {
            "as_of": "2026-06-27T12:00:00",
            "accounts": [
                {"account_id": "roth1", "positions": [
                    {"symbol": "VTI", "description": "Vanguard Total Stock Market ETF",
                     "value": 30000.0, "quantity": 100, "price": 300.0,
                     "cost_basis": 24000.0, "total_gain_loss": 6000.0,
                     "security_type": "etf", "sector": "Diversified"},
                    {"symbol": "VXUS", "description": "Vanguard Total Intl Stock ETF",
                     "value": 11820.0, "quantity": 200, "price": 59.1,
                     "cost_basis": 10500.0, "total_gain_loss": 1320.0,
                     "security_type": "etf", "sector": "International"},
                    {"symbol": "VMFXX", "description": "Vanguard Federal Money Market",
                     "value": 5000.0, "quantity": 5000, "price": 1.0,
                     "cost_basis": 5000.0, "total_gain_loss": 0.0,
                     "security_type": "money market", "is_cash_equivalent": True},
                ]},
            ],
        },
    }


def build_provider_balances():
    return [{
        "account_name": "Shareworks — DEMO",
        "current_value": 21500.00,
        "positions": [
            {"symbol": "DEMO", "description": "Common Stock (available)", "value": 12500.0, "vested": True},
            {"symbol": "DEMO", "description": "Unvested RSUs (pre-tax)", "value": 9000.0, "vested": False},
        ],
        "as_of": "2026-06-27",
        "note": "Sample equity data",
        "vesting_schedule": [
            {"date": "2026-09-15", "shares": 75, "value": 3000.0},
            {"date": "2027-03-15", "shares": 75, "value": 3000.0},
            {"date": "2027-09-15", "shares": 75, "value": 3000.0},
        ],
    }]


def build_config():
    return {
        "baby_keywords": [],
        "ai_keywords": ["anthropic", "claude", "cursor"],
        "household_keywords": ["whole foods", "costco", "trader joe", "target", "walgreens"],
        "health_keywords": [],
        "pet_keywords": ["petsmart"],
        "transport_keywords": ["shell", "transit"],
        "bucket_overrides": {},
    }


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "plaid_transactions.json").write_text(json.dumps(build_transactions(), indent=2))
    (out / "plaid_balances.json").write_text(json.dumps(build_balances(), indent=2))
    (out / "plaid_holdings.json").write_text(json.dumps(build_holdings(), indent=2))
    (out / "provider_balances.json").write_text(json.dumps(build_provider_balances(), indent=2))
    (out / "config.json").write_text(json.dumps(build_config(), indent=2))
    print(f"Sample data written to {out}")
    return out


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "/tmp/finance-viz-sample"
    main(target)
