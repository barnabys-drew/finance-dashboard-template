import json
from collections import defaultdict

from flask import Flask, jsonify, render_template

from detect_anomalies import scan as scan_anomalies
from detect_subscriptions import analyze as analyze_subscriptions
from forecast_cashflow import analyze as analyze_cashflow
from finance_config import (COLORS, PLAID_DIR, VALID_BUCKETS, assign_bucket,
                            bucket_keyword_lists, load_finance_config)
from networth import load_networth
from plaid_data import load_walgreens_fills
from portfolio_analytics import analyze_portfolio
from position_news import load_position_news
from statements import merchant_name
from transactions import load_transactions

app = Flask(__name__)


@app.route('/api/networth')
def api_networth():
    accounts = load_networth()
    total = sum(a['current_value'] for a in accounts)
    total_gain = sum(a['total_gain_loss'] for a in accounts)
    by_type = defaultdict(float)
    for a in accounts:
        by_type[a['account_name']] += a['current_value']
    return jsonify({
        'accounts': accounts,
        'total': round(total, 2),
        'total_gain_loss': round(total_gain, 2),
        'by_account': dict(by_type),
        'analytics': analyze_portfolio(accounts, load_finance_config()),
    })


@app.route('/api/position-news')
def api_position_news():
    return jsonify(load_position_news())


@app.route('/api/subscriptions')
def api_subscriptions():
    res = analyze_subscriptions()
    return jsonify(res or {
        "subscriptions": [], "bills": [], "price_changes": [],
        "lapsed": [], "emerging": [], "monthly_total": 0,
        "monthly_total_with_bills": 0, "as_of": None,
    })


@app.route('/api/cashflow')
def api_cashflow():
    try:
        return jsonify(analyze_cashflow())
    except Exception as e:
        app.logger.warning("cashflow forecast failed: %s", e)
        return jsonify({"available": False})


@app.route('/api/anomalies')
def api_anomalies():
    try:
        return jsonify(scan_anomalies())
    except Exception as e:
        app.logger.warning("anomaly scan failed: %s", e)
        return jsonify({"month": None, "transaction_count": 0, "findings": []})


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data')
def api_data():
    txns = load_transactions()
    expenses = [t for t in txns if not t['is_income'] and not t.get('is_savings')]
    income_txns = [t for t in txns if t['is_income']]
    savings_txns = [t for t in txns if t.get('is_savings')]

    by_cat = defaultdict(float)
    for t in expenses:
        by_cat[t['category']] += t['amount']

    by_date = defaultdict(float)
    by_month = defaultdict(float)
    by_month_cat = defaultdict(lambda: defaultdict(float))
    income_by_month = defaultdict(float)
    for t in expenses:
        by_date[t['date']] += t['amount']
        mo = t['date'][:7]
        by_month[mo] += t['amount']
        by_month_cat[t['category']][mo] += t['amount']
    for t in income_txns:
        income_by_month[t['date'][:7]] += t['amount']

    by_merchant = defaultdict(float)
    for t in expenses:
        by_merchant[merchant_name(t['description'])] += t['amount']
    top_merchants = sorted(by_merchant.items(), key=lambda x: -x[1])[:12]

    total_exp = sum(t['amount'] for t in expenses)
    total_inc = sum(t['amount'] for t in income_txns)
    total_savings = sum(t['amount'] for t in savings_txns)
    cfg_path = PLAID_DIR / "config.json"
    cfg = {}
    if cfg_path.exists():
        with open(cfg_path) as f:
            cfg = json.load(f)
    baby_kws = cfg.get("baby_keywords", [])
    health_kws = cfg.get("health_keywords", [])
    pet_kws = cfg.get("pet_keywords", [])
    household_kws = cfg.get("household_keywords", [])
    transport_kws = cfg.get("transport_keywords", [])
    ai_kws = cfg.get("ai_keywords", [])
    utility_kws = cfg.get("utility_keywords", [])
    # Per-bucket monthly budgets (config.json `budgets`): validate here so the
    # frontend can trust every key is a real bucket and every value a positive
    # number — invalid entries are silently dropped and the tile renders as if
    # unbudgeted rather than erroring.
    budgets = {k: float(v) for k, v in cfg.get("budgets", {}).items()
               if k in VALID_BUCKETS and isinstance(v, (int, float))
               and not isinstance(v, bool) and v > 0}
    overrides = cfg.get("bucket_overrides", {})
    wg_fills = load_walgreens_fills()
    wg_match_days = int(cfg.get("walgreens", {}).get("match_days", 10))

    # Assign each expense to a SINGLE insight bucket via the shared classifier in
    # finance_config (the single source of truth — detect_subscriptions.py uses the
    # same function so the recurring scan can't drift from the badge tiles). The
    # bucket is stamped onto each transaction so the frontend reads t.bucket instead
    # of re-deriving the rules. Per-charge overrides come from /finance-recategorize.
    kw = bucket_keyword_lists(cfg)
    _bucket = defaultdict(float)
    for t in expenses:
        b = assign_bucket(t, kw)
        t['bucket'] = b
        _bucket[b] += t['amount']
    dd_total       = _bucket['food']
    sub_total      = _bucket['subs']
    shopping_total = _bucket['shopping']
    baby_total     = _bucket['baby']
    ai_total       = _bucket['ai']
    health_total   = _bucket['health']
    pet_total      = _bucket['pet']
    household_total = _bucket['household']
    atm_cash       = _bucket['cash']
    electricity    = _bucket['electricity']
    insurance_total = _bucket['insurance']
    rent_total     = _bucket['rent']
    transport_total = _bucket['transport']
    dining_total   = _bucket['dining']
    entertainment_total = _bucket['entertainment']
    personal_total = _bucket['personal']
    other_total    = _bucket['other']
    debit_instore = sum(
        t['amount'] for t in expenses
        if t.get('payment_channel') == 'in store'
        and not any(m in t['description'].upper() for m in ('WEB ID', 'PPD ID', 'ACH '))
    )

    return jsonify({
        'transactions': txns,
        'baby_keywords': baby_kws,
        'health_keywords': health_kws,
        'pet_keywords': pet_kws,
        'household_keywords': household_kws,
        'transport_keywords': transport_kws,
        'ai_keywords': ai_kws,
        'utility_keywords': utility_kws,
        'budgets': budgets,
        'walgreens_fills': wg_fills,
        'walgreens_match_days': wg_match_days,
        'bucket_overrides': overrides,
        'by_category': dict(by_cat),
        'colors': COLORS,
        'by_date': dict(sorted(by_date.items())),
        'top_merchants': top_merchants,
        'by_month': dict(sorted(by_month.items())),
        'by_month_category': {cat: dict(months) for cat, months in by_month_cat.items()},
        'income_by_month': dict(sorted(income_by_month.items())),
        'insights': {
            'total_expenses': round(total_exp, 2),
            'total_income': round(total_inc, 2),
            'savings_transfers': round(total_savings, 2),
            # Saved = income − spend (period cash-flow surplus). Internal transfers
            # to Ally HYSA are reported above as context but excluded here — folding
            # them in counted pre-existing cash as income saved and pushed the rate
            # past 100%. The frontend mirrors this in getViewData/render.
            'savings_rate': round((total_inc - total_exp) / total_inc * 100, 1) if total_inc > 0 else 0,
            'transaction_count': len(expenses),
            'doordash_total': round(dd_total, 2),
            'sub_total': round(sub_total, 2),
            'shopping_total': round(shopping_total, 2),
            'baby_total': round(baby_total, 2),
            'ai_total': round(ai_total, 2),
            'debit_instore': round(debit_instore, 2),
            'atm_cash': round(atm_cash, 2),
            'electricity': round(electricity, 2),
            'health_total': round(health_total, 2),
            'pet_total': round(pet_total, 2),
            'household_total': round(household_total, 2),
            'insurance_total': round(insurance_total, 2),
            'rent_total': round(rent_total, 2),
            'transport_total': round(transport_total, 2),
            'dining_total': round(dining_total, 2),
            'entertainment_total': round(entertainment_total, 2),
            'personal_total': round(personal_total, 2),
            'other_total': round(other_total, 2),
        },
    })

if __name__ == '__main__':
    print("Starting Family Finance Dashboard at http://localhost:5000")
    app.run(debug=True, port=5000, host='0.0.0.0')
