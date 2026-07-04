"""
Scan transactions for unusual spending patterns.
Flags any transaction matching at least one of:
  1. Large transaction: amount > 2x the merchant's historical average (and > $50)
  2. New merchant: first appearance ever in the dataset (and > $30)
  3. Hot category: current month's category total > 1.5x its historical monthly average

Usage:
    python3 detect_anomalies.py [YYYY-MM]   # defaults to most recent full month
"""

import json, os, sys, statistics
from collections import defaultdict
from pathlib import Path
from datetime import date, timedelta

TXN_FILE = Path(os.environ.get("FINANCE_VIZ_DIR", Path.home() / ".finance-viz")) / "plaid_transactions.json"

# Categories that are transfers/fees — not real spending, skip for all signals
SKIP_CATS = {"TRANSFER_OUT", "TRANSFER_IN", "LOAN_PAYMENTS", "BANK_FEES", "INCOME"}

# Categories to skip for hot-category signal only (fixed costs that can't meaningfully spike)
SKIP_HOT_CATS = {"RENT_AND_UTILITIES"}

# Categories to skip for large-transaction signal (amounts naturally vary, not worth flagging)
SKIP_LARGE_CATS = {"PERSONAL_CARE"}

# Raw name prefixes that are always unique (dates/IDs embedded) — skip new-merchant signal
SKIP_NEW_MERCHANT_PREFIXES = (
    "payment to chase card",
    "non-chase atm",
    "ally bank",
    "zelle payment",
    "venmo",
)

def load():
    with open(TXN_FILE) as f:
        txns = json.load(f)
    return [
        t for t in txns
        if not t.get("is_income")
        and not t.get("pending")
        and t.get("plaid_category") not in SKIP_CATS
    ]

def most_recent_full_month(txns):
    today = date.today()
    # If we're past the 5th, current month has enough data; otherwise use prior month
    if today.day > 5:
        return f"{today.year}-{today.month:02d}"
    first = today.replace(day=1) - timedelta(days=1)
    return f"{first.year}-{first.month:02d}"

def scan(target_month: str = None):
    """Return structured anomaly findings for a month (defaults to most recent
    full month). Used by the dashboard's /api/anomalies endpoint and the CLI
    `analyze()` below. Each finding is a JSON-friendly dict with a `kind`
    (`hot_category` | `large` | `new_merchant`), a human `detail`, and the fields
    the UI needs to render a card."""
    txns = load()
    if not target_month:
        target_month = most_recent_full_month(txns)

    current = [t for t in txns if t["date"].startswith(target_month)]
    historical = [t for t in txns if not t["date"].startswith(target_month)]
    if not current:
        return {"month": target_month, "transaction_count": 0, "findings": []}

    findings = []

    # --- Signal 1: large transaction vs merchant history ---
    merchant_history = defaultdict(list)
    for t in historical:
        merchant_history[t["description"]].append(t["amount"])
    for t in current:
        if t.get("plaid_category") in SKIP_LARGE_CATS:
            continue
        hist = merchant_history.get(t["description"], [])
        if len(hist) >= 2 and t["amount"] > 50:
            avg = statistics.mean(hist)
            if avg > 0 and t["amount"] > 2 * avg:
                findings.append({
                    "kind": "large", "date": t["date"], "amount": round(t["amount"], 2),
                    "merchant": t["description"], "category": t.get("plaid_category"),
                    "ratio": round(t["amount"] / avg, 1),
                    "detail": f"${t['amount']:.2f} — {t['amount'] / avg:.1f}× the ${avg:.2f} you usually spend here",
                })

    # --- Signal 2: new merchant (never seen before) ---
    seen_merchants = {t["description"] for t in historical}
    for t in current:
        desc_lower = t["description"].lower()
        if any(desc_lower.startswith(p) for p in SKIP_NEW_MERCHANT_PREFIXES):
            continue
        if t["description"] not in seen_merchants and t["amount"] > 30:
            findings.append({
                "kind": "new_merchant", "date": t["date"], "amount": round(t["amount"], 2),
                "merchant": t["description"], "category": t.get("plaid_category"),
                "detail": f"First charge ever at this merchant (${t['amount']:.2f})",
            })

    # --- Signal 3: hot category this month vs monthly average ---
    month_cat = defaultdict(lambda: defaultdict(float))
    for t in historical:
        month_cat[t["date"][:7]][t.get("plaid_category", "OTHER")] += t["amount"]
    current_cat = defaultdict(float)
    for t in current:
        current_cat[t.get("plaid_category", "OTHER")] += t["amount"]
    for cat, cur_total in current_cat.items():
        if cat in SKIP_HOT_CATS:
            continue
        monthly_totals = [month_cat[m][cat] for m in month_cat if month_cat[m][cat] > 100]
        if len(monthly_totals) >= 3:
            avg = statistics.mean(monthly_totals)
            if avg > 0 and cur_total > 1.5 * avg and cur_total - avg > 50:
                findings.append({
                    "kind": "hot_category", "category": cat, "amount": round(cur_total, 2),
                    "ratio": round(cur_total / avg, 1),
                    "detail": f"${cur_total:.2f} this month vs ${avg:.2f}/mo average ({cur_total / avg:.1f}×)",
                })

    # Category spikes first (biggest ratio), then transaction findings by amount.
    findings.sort(key=lambda f: (f["kind"] != "hot_category", -(f.get("ratio") or 0), -(f.get("amount") or 0)))
    return {"month": target_month, "transaction_count": len(current), "findings": findings}


def analyze(target_month: str):
    res = scan(target_month)
    findings = res["findings"]
    print(f"\nAnomaly report for {res['month']}  "
          f"({res['transaction_count']} transactions, {len(findings)} flags)\n")
    if not res["transaction_count"]:
        print(f"No transactions found for {res['month']}.")
        return
    if not findings:
        print("  Nothing unusual detected.")
        return
    cat_flags = [f for f in findings if f["kind"] == "hot_category"]
    txn_flags = [f for f in findings if f["kind"] != "hot_category"]
    if txn_flags:
        print("Transaction-level flags:")
        for f in txn_flags:
            print(f"  {f['date']}  ${f['amount']:8.2f}  {f['merchant']:<35s}  ← {f['detail']}")
    if cat_flags:
        print("\nCategory-level flags:")
        for f in cat_flags:
            print(f"  hot category '{f['category']}': {f['detail']}")
    print()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if not target:
        txns = load()
        target = most_recent_full_month(txns)
    analyze(target)
