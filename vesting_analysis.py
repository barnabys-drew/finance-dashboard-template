"""
Analyze the employer RSU vesting schedule for exit timing.

Reads the per-vest-event schedule scraped from Shareworks (stored in
provider_balances.json) and answers: which months vest the most shares (and
dollar value), when the next vests land, cumulative shares available over time,
and a per-year rollup.

Usage:
    python3 vesting_analysis.py            # full report, future vests only
    python3 vesting_analysis.py 2027       # only that calendar year
    python3 vesting_analysis.py --all      # include already-vested past events
    python3 vesting_analysis.py --price 60 # re-price at a given share price
"""

import json, sys, re
from collections import defaultdict
from datetime import date
from pathlib import Path

PROVIDER_FILE = Path.home() / ".finance-viz" / "provider_balances.json"
MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_month(ym):
    y, m = ym.split("-")
    return f"{MONTHS[int(m)]} {y}"


def load_schedule():
    if not PROVIDER_FILE.exists():
        return None, [], None, 0.0
    data = json.load(open(PROVIDER_FILE))
    for p in data:
        sched = p.get("vesting_schedule") or []
        if sched:
            total_sh = sum(t.get("shares") or 0 for t in sched)
            total_val = sum(t.get("value") or 0 for t in sched)
            price = (total_val / total_sh) if total_sh else 0.0
            return p["account_name"], sched, p.get("as_of"), price
    return None, [], None, 0.0


def main(argv):
    year = next((a for a in argv if re.fullmatch(r"20\d\d", a)), None)
    include_past = "--all" in argv
    price_override = None
    if "--price" in argv:
        try:
            price_override = float(argv[argv.index("--price") + 1])
        except (ValueError, IndexError):
            pass

    name, sched, as_of, price = load_schedule()
    if not sched:
        print("No vesting schedule found. Run /finance to refresh Shareworks first.")
        return
    if price_override:
        price = price_override

    today = date.today().isoformat()
    rows = []
    for t in sched:
        d = t.get("date")
        if not d:
            continue
        if not include_past and d < today:
            continue
        if year and not d.startswith(year):
            continue
        shares = t.get("shares") or 0
        value = shares * price if price_override else (t.get("value") or 0)
        rows.append({"date": d, "shares": shares, "value": value})

    if not rows:
        print(f"No {'' if include_past else 'future '}vesting events"
              f"{' in ' + year if year else ''}.")
        return

    rows.sort(key=lambda r: r["date"])
    total_sh = sum(r["shares"] for r in rows)
    total_val = sum(r["value"] for r in rows)

    by_month = defaultdict(lambda: {"shares": 0.0, "value": 0.0, "n": 0})
    for r in rows:
        m = by_month[r["date"][:7]]
        m["shares"] += r["shares"]; m["value"] += r["value"]; m["n"] += 1

    by_year = defaultdict(lambda: {"shares": 0.0, "value": 0.0})
    for r in rows:
        y = by_year[r["date"][:4]]
        y["shares"] += r["shares"]; y["value"] += r["value"]

    scope = f" — {year}" if year else ""
    kind = "all" if include_past else "future"
    print(f"\n{name} — RSU vesting analysis{scope}  ({kind}, as of {as_of})")
    print(f"Share price used: ${price:,.2f}"
          + ("  (override)" if price_override else "  (from last scrape)"))
    print(f"Total {kind} vesting: {total_sh:,.0f} shares  ≈ ${total_val:,.2f} (pre-tax)\n")

    nxt = rows[0]
    print(f"Next vest: {nxt['date']}  —  {nxt['shares']:,.0f} shares  ≈ ${nxt['value']:,.2f}")

    print("\nBiggest months by shares:")
    ranked = sorted(by_month.items(), key=lambda kv: (-kv[1]["shares"], kv[0]))
    for ym, v in ranked[:8]:
        print(f"  {fmt_month(ym):<9s} {v['shares']:>6,.0f} shares   ${v['value']:>10,.2f}"
              f"   ({v['n']} event{'s' if v['n'] != 1 else ''})")

    print("\nBy year:")
    for y in sorted(by_year):
        v = by_year[y]
        print(f"  {y}: {v['shares']:>6,.0f} shares   ${v['value']:>11,.2f}")

    print("\nFull schedule (cumulative):")
    cum_sh = cum_val = 0.0
    for r in rows:
        cum_sh += r["shares"]; cum_val += r["value"]
        print(f"  {r['date']}  {r['shares']:>5,.0f} sh  ${r['value']:>9,.2f}"
              f"   | cumulative {cum_sh:>6,.0f} sh  ${cum_val:>11,.2f}")
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
