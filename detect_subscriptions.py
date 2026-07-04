"""
Detect recurring charges from transaction history — no hardcoded merchant list.

A merchant is "recurring" when its charges fall on a regular cadence (weekly,
biweekly, monthly, quarterly, semiannual, or annual). Recurring merchants are
split by amount stability:
  - subscription : regular cadence + stable amount (e.g. YouTube, Amazon Prime)
  - recurring bill: regular cadence + variable amount (e.g. ComEd, Verizon)

Also reports:
  - price changes : latest charge differs from the prior typical amount
  - possibly lapsed: a recurring charge that has missed its expected cycle
  - emerging       : only two charges so far, cadence not yet confirmed

Usage:
    python3 detect_subscriptions.py            # scan all history
    python3 detect_subscriptions.py --json     # machine-readable output
"""

import json, os, sys, statistics
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from finance_config import (BUCKET_LABELS, _categorize_plaid, assign_bucket,
                            bucket_keyword_lists, load_finance_config)

_BASE_DIR = Path(os.environ.get("FINANCE_VIZ_DIR", Path.home() / ".finance-viz"))
TXN_FILE = _BASE_DIR / "plaid_transactions.json"
CONFIG_FILE = _BASE_DIR / "config.json"


def ai_keywords():
    """Merchant keywords for AI spend, kept out of the subscription scan.

    These charges (Anthropic/Claude, Cursor, Sakana, OpenAI) — both recurring AI
    subscriptions and ad-hoc, usage-based billing — are consolidated into the
    dashboard's own AI tile rather than the Subscriptions tile, so all AI spend
    lands in one place. Mirrors ai_keywords in config.json so there's a single
    source of truth with the badge bucketing.
    """
    try:
        with open(CONFIG_FILE) as f:
            return [k.lower() for k in json.load(f).get("ai_keywords", [])]
    except (OSError, ValueError):
        return []

# Not real discretionary spend / can't be a subscription — drop before grouping.
SKIP_CATS = {"TRANSFER_OUT", "TRANSFER_IN", "LOAN_PAYMENTS", "LOAN_DISBURSEMENTS", "INCOME"}

# Discretionary spend that recurs by coincidence, not commitment. Two restaurant
# visits ~a month apart look exactly like a monthly bill to the cadence check, so
# food & dining (restaurants, delivery like DoorDash, groceries) churned out false
# "emerging subscription" hits (e.g. JIN, Laplancha). Dining isn't a subscription —
# drop the whole category before grouping.
NON_SUBSCRIPTION_CATS = {"FOOD_AND_DRINK"}

# Raw-name prefixes that recur but aren't subscriptions (card payments, ATM, p2p).
SKIP_PREFIXES = (
    "payment to chase card",
    "non-chase atm",
    "ally bank",
    "zelle payment",
    "venmo",
)

# Cadence buckets: (label, low_days, high_days, charges_per_year).
# Deliberately monthly-or-longer: weekly/biweekly "regularity" at a merchant is
# almost always frequent shopping (a restaurant, gas, groceries), not a bill.
CADENCES = [
    ("monthly",    24,  37, 12.0),
    ("quarterly",  85,  100, 4.0),
    ("semiannual", 170, 200, 2.0),
    ("annual",     350, 385, 1.0),
]

STABLE_CV = 0.12          # coeff. of variation below this = stable amount
PRICE_CHANGE_FRAC = 0.05  # > 5% move flags a price change
PRICE_CHANGE_MIN = 1.0    # and at least $1


def load():
    with open(TXN_FILE) as f:
        txns = json.load(f)
    ai_kws = ai_keywords()
    cfg = load_finance_config()
    kw = bucket_keyword_lists(cfg)
    keep = []
    for t in txns:
        if t.get("is_income") or t.get("pending"):
            continue
        if t.get("plaid_category") in SKIP_CATS or t.get("plaid_category") in NON_SUBSCRIPTION_CATS:
            continue
        low = t["description"].lower()
        if any(low.startswith(p) for p in SKIP_PREFIXES):
            continue
        if any(k in low for k in ai_kws):  # ad-hoc AI usage -> AI tile, not a subscription
            continue
        # Tag each charge with the same insight bucket the dashboard tiles use, so
        # the recurring scan attributes a charge to its real badge (State Farm →
        # Insurance, Verizon → Utilities) instead of lumping every stable monthly
        # charge under "Subscriptions". plaid_transactions.json has no display
        # category yet, so derive it the same way load_plaid_transactions does.
        raw_name = t.get("raw_name", t["description"])
        t["category"] = _categorize_plaid(raw_name, t.get("plaid_category") or "")
        t["bucket"] = assign_bucket(t, kw)
        keep.append(t)
    return keep


def parse(d):
    return date.fromisoformat(d[:10])


def cadence_for(median_gap):
    for label, lo, hi, per_year in CADENCES:
        if lo <= median_gap <= hi:
            return label, per_year
    return None, None


def is_regular(gaps, median_gap):
    """All gaps within tolerance of the median, allowing one outlier."""
    tol = max(7, 0.4 * median_gap)
    bad = sum(1 for g in gaps if abs(g - median_gap) > tol)
    return bad <= 1


def analyze():
    txns = load()
    if not txns:
        return None

    overall_last = max(parse(t["date"]) for t in txns)

    # Group by merchant, collapsing same-day charges into one billing event.
    by_merchant = defaultdict(lambda: defaultdict(float))  # name -> date -> amount
    bucket_votes = defaultdict(Counter)                    # name -> bucket -> count
    for t in txns:
        by_merchant[t["description"]][parse(t["date"])] += t["amount"]
        bucket_votes[t["description"]][t["bucket"]] += 1

    subscriptions, bills, emerging, lapsed, price_changes = [], [], [], [], []

    for name, day_totals in by_merchant.items():
        dates = sorted(day_totals)
        if len(dates) < 2:
            continue
        amounts = [day_totals[d] for d in dates]
        gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        median_gap = statistics.median(gaps)
        cadence, per_year = cadence_for(median_gap)
        if cadence is None or not is_regular(gaps, median_gap):
            continue

        latest = amounts[-1]
        last_date = dates[-1]
        monthly_equiv = latest * per_year / 12.0
        mean_amt = statistics.mean(amounts)
        cv = (statistics.pstdev(amounts) / mean_amt) if mean_amt else 0.0

        bucket = bucket_votes[name].most_common(1)[0][0]
        rec = {
            "merchant": name,
            "cadence": cadence,
            "latest": round(latest, 2),
            "monthly_equiv": round(monthly_equiv, 2),
            "annual": round(latest * per_year, 2),
            "last_date": last_date.isoformat(),
            "count": len(dates),
            "bucket": bucket,
            "category": BUCKET_LABELS.get(bucket, "Other"),
        }

        # Only two data points: cadence is a guess, not yet confirmed.
        if len(dates) == 2:
            emerging.append(rec)
            continue

        # Price change vs the typical prior amount — only meaningful for
        # stable-amount subscriptions, where a move is a real price change
        # rather than ordinary bill-to-bill variance.
        prior = amounts[:-1]
        prior_med = statistics.median(prior)
        if (cv <= STABLE_CV and prior_med
                and abs(latest - prior_med) > max(PRICE_CHANGE_MIN, PRICE_CHANGE_FRAC * prior_med)):
            price_changes.append({**rec, "prev": round(prior_med, 2),
                                  "delta": round(latest - prior_med, 2)})

        # Missed its expected cycle → possibly cancelled.
        if (overall_last - last_date).days > median_gap * 1.5 + 5:
            lapsed.append({**rec, "days_silent": (overall_last - last_date).days})
        # A recurring charge is a "subscription" only when it lands in the
        # Subscriptions badge (bucket 'subs') — that's what keeps this list in
        # lockstep with the dashboard tile. Everything else recurring (insurance,
        # utilities, rent, …) is a bill attributed to its own badge, regardless of
        # whether the amount happens to be stable (State Farm is stable but is
        # Insurance, not a subscription).
        elif bucket == "subs":
            subscriptions.append(rec)
        else:
            bills.append(rec)

    subscriptions.sort(key=lambda r: -r["monthly_equiv"])
    bills.sort(key=lambda r: -r["monthly_equiv"])
    emerging.sort(key=lambda r: -r["monthly_equiv"])

    return {
        "as_of": overall_last.isoformat(),
        "subscriptions": subscriptions,
        "bills": bills,
        "price_changes": price_changes,
        "lapsed": lapsed,
        "emerging": emerging,
        "monthly_total": round(sum(r["monthly_equiv"] for r in subscriptions), 2),
        "monthly_total_with_bills": round(
            sum(r["monthly_equiv"] for r in subscriptions + bills), 2),
    }


def print_report(res):
    if not res:
        print("No transaction data found.")
        return

    def row(r, tag=""):
        print(f"  {r['merchant']:<28s} {r['cadence']:<10s} "
              f"${r['latest']:>8.2f}  (~${r['monthly_equiv']:>7.2f}/mo){tag}")

    print(f"\nRecurring-charge report  (as of {res['as_of']})\n")

    print(f"Subscriptions — {len(res['subscriptions'])} found, "
          f"~${res['monthly_total']:.2f}/mo (${res['monthly_total']*12:.2f}/yr):")
    if res["subscriptions"]:
        for r in res["subscriptions"]:
            row(r)
    else:
        print("  (none detected)")

    if res["bills"]:
        print(f"\nRecurring bills (variable amount) — {len(res['bills'])}:")
        for r in res["bills"]:
            row(r)

    if res["price_changes"]:
        print("\nPrice changes:")
        for r in res["price_changes"]:
            arrow = "↑" if r["delta"] > 0 else "↓"
            print(f"  {r['merchant']:<28s} ${r['prev']:.2f} → ${r['latest']:.2f}  "
                  f"{arrow} ${abs(r['delta']):.2f}")

    if res["lapsed"]:
        print("\nPossibly lapsed / cancelled (missed expected cycle):")
        for r in res["lapsed"]:
            print(f"  {r['merchant']:<28s} {r['cadence']:<10s} "
                  f"last seen {r['last_date']}  ({r['days_silent']}d silent)")

    if res["emerging"]:
        print("\nEmerging (2 charges — watch for a third to confirm):")
        for r in res["emerging"]:
            row(r)

    print()


if __name__ == "__main__":
    res = analyze()
    if "--json" in sys.argv[1:]:
        print(json.dumps(res, indent=2))
    else:
        print_report(res)
