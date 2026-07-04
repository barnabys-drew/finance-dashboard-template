"""
Cash-flow forecast: project the checking balance forward from three ingredients
the data already gives us —

  1. paychecks   : payroll cadence + typical amount from income history
  2. bills       : recurring merchants from detect_subscriptions (next expected
                   date = last seen + cadence)
  3. daily burn  : average daily discretionary (non-recurring) spend over
                   complete months

and report the minimum projected balance plus a "safe to move to savings"
number (minimum minus a buffer). All date math is stdlib — no dateutil.

Usage:
    python3 forecast_cashflow.py           # human-readable report
    python3 forecast_cashflow.py --json    # machine-readable output
"""

import calendar
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import detect_subscriptions
from detect_subscriptions import SKIP_CATS, SKIP_PREFIXES
from networth import load_networth
from statements import merchant_name

_BASE_DIR = Path(os.environ.get("FINANCE_VIZ_DIR", Path.home() / ".finance-viz"))
TXN_FILE = _BASE_DIR / "plaid_transactions.json"
CONFIG_FILE = _BASE_DIR / "config.json"

# Months per billing cycle for each detect_subscriptions cadence label.
_CADENCE_MONTHS = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}

_DEFAULTS = {
    "buffer": 2000,
    "horizon_days": 60,
    "payroll_keywords": ["payroll"],
    "checking_account": "Chase Checking",
}


def _cfg():
    try:
        with open(CONFIG_FILE) as f:
            user = json.load(f).get("cashflow", {})
    except (OSError, ValueError):
        user = {}
    return {**_DEFAULTS, **user}


def _load_txns():
    try:
        with open(TXN_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _parse(d):
    return date.fromisoformat(d[:10])


def add_months(d, months):
    """d plus N calendar months, clamping the day (Jan 31 + 1mo -> Feb 28)."""
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _month_complete(m, first, last):
    """True when [first, last] spans the whole calendar month "YYYY-MM"."""
    y, mo = map(int, m.split("-"))
    return (first <= date(y, mo, 1)
            and last >= date(y, mo, calendar.monthrange(y, mo)[1]))


def _paychecks(txns, keywords, today, horizon_end):
    """Detect payroll cadence + typical amount PER PAYROLL SOURCE, then project
    each stream's future paydays.

    Streams are grouped by transaction description because a household can have
    several payrolls on different cadences (e.g. a semimonthly salary plus a
    weekly one) — pooling their dates would produce a nonsense cadence. Per
    stream, amount is the MEDIAN of the last 6 paydays — deliberately, so a
    one-off bonus or RSU-withholding month doesn't inflate the projection —
    and cadence is the median gap (biweekly ~14; semimonthly gaps of 13-17
    land on ~15, close enough for a 60-day horizon). Streams with fewer than
    2 paydays are skipped; no usable stream → income-free forecast, flagged.
    """
    kws = [k.lower() for k in keywords]
    streams = defaultdict(lambda: defaultdict(float))  # desc -> date -> amount
    for t in txns:
        if not t.get("is_income") or t.get("pending"):
            continue
        if not any(k in t["description"].lower() for k in kws):
            continue
        streams[t["description"]][_parse(t["date"])] += t["amount"]

    projected, stream_assumptions = [], []
    for desc, by_day in streams.items():
        days = sorted(by_day)
        if len(days) < 2:
            continue
        gaps = [(days[i] - days[i - 1]).days for i in range(1, len(days))]
        cadence = max(1, round(statistics.median(gaps)))
        amount = round(statistics.median([by_day[d] for d in days[-6:]]), 2)
        name = merchant_name(desc)
        stream_assumptions.append(
            {"name": name, "cadence_days": cadence, "amount": amount})
        nxt = days[-1] + timedelta(days=cadence)
        while nxt <= horizon_end:
            if nxt > today:
                projected.append({"date": nxt.isoformat(), "amount": amount,
                                  "name": name})
            nxt += timedelta(days=cadence)

    projected.sort(key=lambda p: p["date"])
    stream_assumptions.sort(key=lambda s: -s["amount"])
    main = stream_assumptions[0] if stream_assumptions else None
    return projected, {
        "payroll_cadence_days": main["cadence_days"] if main else None,
        "payroll_amount": main["amount"] if main else None,
        "payroll_streams": stream_assumptions,
    }


def _upcoming_bills(recurring, today, horizon_end):
    """Next expected occurrences of each recurring merchant within the horizon.

    Steps last_date forward by the cadence; monthly bills can appear twice in a
    60-day horizon, annual ones only when actually due. An occurrence that is
    already past due (but not yet flagged lapsed by detect_subscriptions) is
    scheduled for tomorrow rather than dropped — the money is still owed.
    """
    out = []
    for rec in recurring:
        months = _CADENCE_MONTHS.get(rec["cadence"])
        if not months:
            continue
        last = _parse(rec["last_date"])
        k = 1
        while True:
            nxt = add_months(last, k * months)
            if nxt > horizon_end:
                break
            due = max(nxt, today + timedelta(days=1))  # overdue -> tomorrow
            out.append({"date": due.isoformat(), "merchant": rec["merchant"],
                        "amount": rec["latest"], "cadence": rec["cadence"],
                        "bucket": rec["bucket"], "category": rec["category"]})
            k += 1
    out.sort(key=lambda b: b["date"])
    return out


def _daily_burn(txns, recurring_names):
    """Average daily discretionary spend: expenses minus transfers/CC-payment
    lines (SKIP_CATS / SKIP_PREFIXES, same exclusions as the recurring scan so
    nothing is double-counted) minus every recurring merchant's charges (those
    are modeled individually as bills). Complete months only; if none exist yet,
    fall back to the whole window and flag it via burn_months=[].

    Card spend is treated as checking outflow: the Sapphire charges count here
    and the "payment to chase card" lines are excluded, so the flow is counted
    exactly once.
    """
    spend = []
    for t in txns:
        if t.get("is_income") or t.get("pending"):
            continue
        if t.get("plaid_category") in SKIP_CATS:
            continue
        low = t["description"].lower()
        if any(low.startswith(p) for p in SKIP_PREFIXES):
            continue
        if t["description"] in recurring_names:
            continue
        spend.append(t)
    if not spend:
        return 0.0, []

    dates = sorted(_parse(t["date"]) for t in spend)
    first, last = dates[0], dates[-1]
    months = sorted({t["date"][:7] for t in spend})
    complete = [m for m in months if _month_complete(m, first, last)]

    if complete:
        total = sum(t["amount"] for t in spend if t["date"][:7] in complete)
        days = sum(calendar.monthrange(*map(int, m.split("-")))[1] for m in complete)
    else:
        total = sum(t["amount"] for t in spend)
        days = max(1, (last - first).days + 1)
    return round(total / days, 2), complete


def analyze(horizon_days=None, today=None):
    """Full forecast. `today` is injectable for tests; defaults to date.today()."""
    cfg = _cfg()
    today = today or date.today()
    horizon = max(14, min(90, int(horizon_days or cfg["horizon_days"])))
    horizon_end = today + timedelta(days=horizon)

    # Starting balance — the configured checking account from net worth.
    accounts = load_networth() or []
    checking = next((a for a in accounts
                     if a.get("account_name") == cfg["checking_account"]), None)
    if checking is None:
        return {"available": False,
                "reason": f"no balance for {cfg['checking_account']!r}"}
    balance = float(checking["current_value"])

    txns = _load_txns()
    subs = detect_subscriptions.analyze() or {}
    # lapsed/emerging are excluded: lapsed is likely cancelled, emerging is an
    # unconfirmed cadence — neither belongs in a forward bill schedule.
    recurring = (subs.get("subscriptions") or []) + (subs.get("bills") or [])
    # Burn excludes ALL recurring-ish merchants (incl. lapsed/emerging): bills
    # are modeled individually above, lapsed history shouldn't project forward,
    # and emerging charges are too ambiguous to double-count either way.
    recurring_names = {r["merchant"]
                       for key in ("subscriptions", "bills", "lapsed", "emerging")
                       for r in (subs.get(key) or [])}

    paychecks, pay_assume = _paychecks(txns, cfg["payroll_keywords"], today, horizon_end)
    bills = _upcoming_bills(recurring, today, horizon_end)
    burn, burn_months = _daily_burn(txns, recurring_names)

    pay_by_day, bill_by_day = {}, {}
    for p in paychecks:
        pay_by_day.setdefault(p["date"], []).append(p)
    for b in bills:
        bill_by_day.setdefault(b["date"], []).append(b)

    daily, min_balance, min_date = [], balance, today.isoformat()
    for i in range(1, horizon + 1):
        d = (today + timedelta(days=i)).isoformat()
        events = []
        for p in pay_by_day.get(d, []):
            balance += p["amount"]
            events.append({"type": "paycheck", "name": p.get("name", "Payroll"),
                           "amount": p["amount"]})
        for b in bill_by_day.get(d, []):
            balance -= b["amount"]
            events.append({"type": "bill", "name": b["merchant"], "amount": b["amount"]})
        balance -= burn
        daily.append({"date": d, "balance": round(balance, 2), "events": events})
        if balance < min_balance:
            min_balance, min_date = balance, d

    buffer = float(cfg["buffer"])
    return {
        "available": True,
        "as_of": today.isoformat(),
        "start_balance": round(float(checking["current_value"]), 2),
        "horizon_days": horizon,
        "buffer": buffer,
        "daily": daily,
        "min_balance": round(min_balance, 2),
        "min_date": min_date,
        "safe_to_save": round(max(0.0, min_balance - buffer), 2),
        "shortfall": min_balance < buffer,
        "upcoming_bills": bills,
        "paychecks": paychecks,
        "assumptions": {**pay_assume, "daily_burn": burn, "burn_months": burn_months},
    }


def print_report(res):
    if not res.get("available"):
        print(f"Forecast unavailable: {res.get('reason', 'unknown')}")
        return
    a = res["assumptions"]
    print(f"\nCash-flow forecast  (as of {res['as_of']}, {res['horizon_days']}d horizon)\n")
    print(f"  Start balance : ${res['start_balance']:,.2f}")
    print(f"  Min projected : ${res['min_balance']:,.2f} on {res['min_date']}")
    print(f"  Safe to save  : ${res['safe_to_save']:,.2f}  (buffer ${res['buffer']:,.0f})")
    if a["payroll_streams"]:
        for s in a["payroll_streams"]:
            print(f"  Assuming ${s['amount']:,.2f} from {s['name']} "
                  f"every {s['cadence_days']}d")
    else:
        print("  No payroll history detected — income-free forecast")
    print(f"  Burn: ${a['daily_burn']:,.2f}/day discretionary "
          f"(from {len(a['burn_months'])} complete months)")
    if res["upcoming_bills"]:
        print(f"\nUpcoming bills ({len(res['upcoming_bills'])}):")
        for b in res["upcoming_bills"]:
            print(f"  {b['date']}  {b['merchant']:<28s} ${b['amount']:>8.2f}  ({b['cadence']})")


if __name__ == "__main__":
    result = analyze()
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))
    else:
        print_report(result)
