"""Regression tests for the dashboard features added in this round:
- /api/networth passes per-account vesting_schedule through untouched
- analytics splits employer equity into vested vs unvested (owned = total - unvested)
- /api/data insight blades are mutually exclusive (most specific wins)
- detect_subscriptions flags a stable monthly merchant as a subscription
"""

import json
import app as finance_app
import detect_subscriptions
import detect_anomalies
import finance_config
import walgreens_pharmacy


def test_walgreens_parser_extracts_fill_dates_and_paid_amounts():
    # Mirrors the inner_text shape of the Prescription Records report.
    body = (
        "Prescription Records\n"
        "Omeprazole 20mg Capsules\n"
        "Qty: 90\n"
        "06/24/2026 \nfill date\n$0.00\nprice\n$194.89\ninsurance saved you\n"
        "Novolog U-100 Insulin Vl10ml(Orang)\n"
        "Qty: 20\n"
        "04/10/2026 \nfill date\n$30.00\nprice\n$769.99\ninsurance saved you\n"
    )
    fills = walgreens_pharmacy._parse_fills(body)
    assert fills == [
        {"date": "2026-06-24", "amount": 0.0, "rx": "Omeprazole 20mg Capsules"},
        {"date": "2026-04-10", "amount": 30.0, "rx": "Novolog U-100 Insulin Vl10ml(Orang)"},
    ]


def test_networth_passes_vesting_schedule_and_splits_unvested(monkeypatch):
    accounts = [{
        "account_name": "Shareworks — ACME",
        "current_value": 50000,
        "total_gain_loss": 0,
        "source": "Provider",
        "positions": [
            {"symbol": "ACME", "description": "Vested", "value": 30000, "vested": True},
            {"symbol": "ACME-RSU", "description": "Unvested RSUs", "value": 20000, "vested": False},
        ],
        "vesting_schedule": [
            {"date": "2026-09-15", "shares": 100, "value": 12000},
            {"date": "2027-03-15", "shares": 80, "value": 8000},
        ],
    }]
    monkeypatch.setattr(finance_app, "load_networth", lambda: accounts)
    monkeypatch.setattr(finance_app, "load_finance_config", lambda: {})

    data = finance_app.app.test_client().get("/api/networth").get_json()
    sw = data["accounts"][0]

    # Schedule echoed through verbatim.
    assert [t["shares"] for t in sw["vesting_schedule"]] == [100, 80]

    totals = data["analytics"]["totals"]
    assert totals["unvested_equity"] == 20000
    assert totals["vested_equity"] == 30000
    # Headline "owned" assets exclude the unvested RSUs.
    assert data["total"] - totals["unvested_equity"] == 30000


def _txn(date, desc, amount, category, is_income=False, plaid_category=""):
    return {"date": date, "description": desc, "amount": amount, "category": category,
            "is_income": is_income, "is_savings": False, "payment_channel": "online",
            "plaid_category": plaid_category}


def test_insight_blades_are_mutually_exclusive(tmp_path, monkeypatch):
    # A baby-keyword purchase that is ALSO in the Shopping category must count
    # only as Baby (more specific), never doubled into Shopping.
    (tmp_path / "config.json").write_text(json.dumps(
        {"baby_keywords": ["widget"], "health_keywords": [], "pet_keywords": []}))
    monkeypatch.setattr(finance_app, "PLAID_DIR", tmp_path)
    txns = [
        _txn("2026-06-01", "WIDGET STORE", 100.0, "Shopping"),  # baby kw + Shopping -> baby
        _txn("2026-06-02", "TARGET", 50.0, "Shopping"),         # shopping only
        _txn("2026-06-03", "PAYCHECK", 1000.0, "Income", is_income=True),
    ]
    monkeypatch.setattr(finance_app, "load_transactions", lambda: txns)

    ins = finance_app.app.test_client().get("/api/data").get_json()["insights"]
    assert ins["baby_total"] == 100.0
    assert ins["shopping_total"] == 50.0  # excludes the widget (baby) spend


def test_health_bucket_uses_plaid_medical_category(tmp_path, monkeypatch):
    # The Health blade counts non-Walgreens MEDICAL spend (e.g. a lab/copay) plus
    # explicit health_keywords. (Walgreens has its own split — see below.)
    (tmp_path / "config.json").write_text(json.dumps(
        {"baby_keywords": [], "health_keywords": ["dexcom"], "pet_keywords": []}))
    monkeypatch.setattr(finance_app, "PLAID_DIR", tmp_path)
    txns = [
        _txn("2026-06-01", "QUEST DIAGNOSTICS", 51.48, "Medical", plaid_category="MEDICAL"),
        _txn("2026-06-02", "DEXCOM", 80.0, "Medical", plaid_category=""),
    ]
    monkeypatch.setattr(finance_app, "load_transactions", lambda: txns)

    ins = finance_app.app.test_client().get("/api/data").get_json()["insights"]
    assert ins["health_total"] == 131.48  # MEDICAL lab + Dexcom keyword


def test_pet_bills_excluded_from_health(tmp_path, monkeypatch):
    # Vet charges often get Plaid-tagged MEDICAL; because Pet is matched before
    # Health, they must land in Pet, not Health.
    (tmp_path / "config.json").write_text(json.dumps(
        {"baby_keywords": [], "health_keywords": [], "pet_keywords": ["banfield"]}))
    monkeypatch.setattr(finance_app, "PLAID_DIR", tmp_path)
    txns = [
        _txn("2026-06-01", "BANFIELD PET HOSPITAL", 220.0, "Health", plaid_category="MEDICAL"),
    ]
    monkeypatch.setattr(finance_app, "load_transactions", lambda: txns)

    ins = finance_app.app.test_client().get("/api/data").get_json()["insights"]
    assert ins["pet_total"] == 220.0
    assert ins["health_total"] == 0.0


def test_household_bucket_keyword_driven(tmp_path, monkeypatch):
    # Household is mixed food+household merchants by keyword regardless of the
    # inconsistent Plaid category.
    (tmp_path / "config.json").write_text(json.dumps(
        {"baby_keywords": [], "health_keywords": [], "pet_keywords": [],
         "household_keywords": ["corner market", "costco", "target", "harvest foods"]}))
    monkeypatch.setattr(finance_app, "PLAID_DIR", tmp_path)
    txns = [
        _txn("2026-06-01", "CORNER MARKET INC", 120.0, "Food & Dining"),     # -> household
        _txn("2026-06-02", "Costco", 200.0, "Shopping"),                      # -> household
        _txn("2026-06-03", "Target", 80.0, "Shopping"),                       # now -> household
        _txn("2026-06-04", "HARVEST FOODS MARKET", 25.0, "Food & Dining"),    # -> household
    ]
    monkeypatch.setattr(finance_app, "load_transactions", lambda: txns)

    ins = finance_app.app.test_client().get("/api/data").get_json()["insights"]
    assert ins["household_total"] == 425.0  # all four household merchants
    assert ins["shopping_total"] == 0.0  # all claimed by household


def test_ai_bucket_keyword_driven_and_not_subscription(tmp_path, monkeypatch):
    # Ad-hoc AI usage gets its own AI tile and is matched before the Subscriptions
    # rule, so a charge Plaid tags as a subscription still lands in AI, not subs.
    (tmp_path / "config.json").write_text(json.dumps(
        {"baby_keywords": [], "health_keywords": [], "pet_keywords": [],
         "household_keywords": [], "ai_keywords": ["anthropic", "claude", "sakana", "cursor"]}))
    monkeypatch.setattr(finance_app, "PLAID_DIR", tmp_path)
    txns = [
        _txn("2026-06-25", "ANTHROPIC", 42.10, "Subscriptions"),   # -> ai, not subs
        _txn("2026-06-26", "CLAUDE.AI", 20.00, "Subscriptions"),   # -> ai
        _txn("2026-06-26", "SAKANA AI", 15.00, "Shopping"),        # -> ai
        _txn("2026-06-27", "CURSOR USAGE", 8.00, "Subscriptions"), # -> ai
        _txn("2026-06-20", "Netflix", 15.99, "Subscriptions"),     # stays subs
    ]
    monkeypatch.setattr(finance_app, "load_transactions", lambda: txns)

    ins = finance_app.app.test_client().get("/api/data").get_json()["insights"]
    assert ins["ai_total"] == 85.10    # Anthropic + Claude + Sakana + Cursor
    assert ins["sub_total"] == 15.99   # only Netflix


def test_bucket_override_reclassifies_single_charge(tmp_path, monkeypatch):
    # A per-charge override (from /finance-recategorize) pulls one Target run out
    # of Baby into Household without touching the keyword lists; an override with
    # an unknown bucket name is ignored and the charge falls back to keywords.
    (tmp_path / "config.json").write_text(json.dumps(
        {"baby_keywords": ["target"], "health_keywords": [], "pet_keywords": [],
         "household_keywords": [],
         "bucket_overrides": {"tid-electronics": "household", "tid-typo": "bogus"}}))
    monkeypatch.setattr(finance_app, "PLAID_DIR", tmp_path)

    def _with_id(tid, desc, amt):
        t = _txn("2026-06-01", desc, amt, "Shopping")
        t["transaction_id"] = tid
        return t
    txns = [
        _with_id("tid-diapers", "Target", 50.0),       # baby keyword -> baby
        _with_id("tid-electronics", "Target", 200.0),  # overridden -> household
        _with_id("tid-typo", "Target", 30.0),          # bad override -> falls back to baby
    ]
    monkeypatch.setattr(finance_app, "load_transactions", lambda: txns)

    ins = finance_app.app.test_client().get("/api/data").get_json()["insights"]
    assert ins["baby_total"] == 80.0        # diapers + typo-fallback
    assert ins["household_total"] == 200.0  # only the overridden charge


def test_walgreens_all_household(tmp_path, monkeypatch):
    # All Walgreens is Household now (the pharmacy/Health split was dropped). Even
    # though Plaid tags the charges MEDICAL, the household keyword is matched first.
    (tmp_path / "config.json").write_text(json.dumps(
        {"baby_keywords": [], "health_keywords": [], "pet_keywords": [],
         "household_keywords": ["walgreens"]}))
    monkeypatch.setattr(finance_app, "PLAID_DIR", tmp_path)
    txns = [
        _txn("2026-06-24", "WALGREENS", 51.48, "Health", plaid_category="MEDICAL"),
        _txn("2026-06-10", "WALGREENS", 23.29, "Health", plaid_category="MEDICAL"),
    ]
    monkeypatch.setattr(finance_app, "load_transactions", lambda: txns)

    ins = finance_app.app.test_client().get("/api/data").get_json()["insights"]
    assert ins["household_total"] == 74.77   # both Walgreens charges
    assert ins["health_total"] == 0.0


def test_buckets_exhaustive_by_plaid_category(tmp_path, monkeypatch):
    # New badges route by Plaid primary category, utilities are peeled off before
    # rent, delivery is separate from dining, and every dollar lands in a bucket
    # (the badge totals sum to total spend — what makes month-over-month honest).
    (tmp_path / "config.json").write_text(json.dumps(
        {"baby_keywords": [], "health_keywords": [], "pet_keywords": [],
         "household_keywords": []}))
    monkeypatch.setattr(finance_app, "PLAID_DIR", tmp_path)
    txns = [
        _txn("2026-06-01", "OAKVIEW PROPERTIES", 2000.0, "Housing", plaid_category="RENT_AND_UTILITIES"),
        _txn("2026-06-02", "ComEd", 100.0, "Utilities", plaid_category="RENT_AND_UTILITIES"),
        _txn("2026-06-03", "State Farm", 150.0, "Insurance", plaid_category="GENERAL_SERVICES"),
        _txn("2026-06-04", "Olive Garden", 40.0, "Food & Dining", plaid_category="FOOD_AND_DRINK"),
        _txn("2026-06-05", "DoorDash", 30.0, "Food & Dining", plaid_category="GENERAL_MERCHANDISE"),
        _txn("2026-06-06", "Amtrak", 25.0, "Transportation", plaid_category="TRANSPORTATION"),
        _txn("2026-06-07", "Steam", 60.0, "Entertainment", plaid_category="ENTERTAINMENT"),
        _txn("2026-06-08", "Hair Salon", 70.0, "Other", plaid_category="PERSONAL_CARE"),
        _txn("2026-06-09", "UPS Store", 18.0, "Other", plaid_category="GENERAL_SERVICES"),
    ]
    monkeypatch.setattr(finance_app, "load_transactions", lambda: txns)

    ins = finance_app.app.test_client().get("/api/data").get_json()["insights"]
    assert ins["rent_total"] == 2000.0
    assert ins["electricity"] == 100.0        # utilities peeled off before rent
    assert ins["insurance_total"] == 150.0
    assert ins["dining_total"] == 40.0
    assert ins["doordash_total"] == 30.0      # delivery stays separate from dining
    assert ins["transport_total"] == 25.0
    assert ins["entertainment_total"] == 60.0
    assert ins["personal_total"] == 70.0
    assert ins["other_total"] == 18.0         # GENERAL_SERVICES (non-insurance) -> other
    badge_sum = sum(ins[k] for k in (
        "rent_total", "electricity", "insurance_total", "dining_total", "doordash_total",
        "transport_total", "entertainment_total", "personal_total", "other_total",
        "baby_total", "pet_total", "health_total", "household_total", "shopping_total",
        "sub_total", "ai_total", "atm_cash"))
    assert round(badge_sum, 2) == round(ins["total_expenses"], 2)


def test_transport_keyword_routes_gas_station(tmp_path, monkeypatch):
    # Gas stations Plaid tags GENERAL_MERCHANDISE (e.g. Casey's) route to Transport
    # via transport_keywords instead of falling into the Shopping catch-all.
    (tmp_path / "config.json").write_text(json.dumps(
        {"baby_keywords": [], "health_keywords": [], "pet_keywords": [],
         "household_keywords": [], "transport_keywords": ["casey"]}))
    monkeypatch.setattr(finance_app, "PLAID_DIR", tmp_path)
    txns = [_txn("2026-06-01", "CASEYS #6430", 78.75, "Shopping", plaid_category="GENERAL_MERCHANDISE")]
    monkeypatch.setattr(finance_app, "load_transactions", lambda: txns)

    ins = finance_app.app.test_client().get("/api/data").get_json()["insights"]
    assert ins["transport_total"] == 78.75
    assert ins["shopping_total"] == 0.0


def test_detect_subscriptions_flags_stable_monthly_merchant(tmp_path, monkeypatch):
    txn_file = tmp_path / "plaid_transactions.json"
    txns = [
        {"date": f"2026-0{m}-15", "description": "Netflix", "amount": 15.99,
         "is_income": False, "pending": False, "plaid_category": ""}
        for m in (3, 4, 5, 6)
    ]
    txn_file.write_text(json.dumps(txns))
    monkeypatch.setattr(detect_subscriptions, "TXN_FILE", txn_file)
    # Hermetic config: default category rules (Netflix → Subscriptions) and no
    # user keyword lists, so the machine's real ~/.finance-viz can't leak in.
    monkeypatch.setattr(detect_subscriptions, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(detect_subscriptions, "load_finance_config", lambda: {})
    monkeypatch.setattr(finance_config, "CATEGORIES", finance_config._DEFAULT_CATEGORIES)

    res = detect_subscriptions.analyze()
    names = [s["merchant"] for s in res["subscriptions"]]
    assert "Netflix" in names
    assert res["monthly_total"] > 0


def test_detect_subscriptions_excludes_ai_keywords(tmp_path, monkeypatch):
    # Ad-hoc AI charges (config ai_keywords) are filtered out of the recurring scan
    # even if they happen to land on a monthly cadence — they belong to the AI tile.
    txn_file = tmp_path / "plaid_transactions.json"
    (tmp_path / "config.json").write_text(json.dumps({"ai_keywords": ["anthropic"]}))
    txns = [
        {"date": f"2026-0{m}-15", "description": desc, "amount": 20.00,
         "is_income": False, "pending": False, "plaid_category": ""}
        for m in (3, 4, 5, 6) for desc in ("ANTHROPIC", "Netflix")
    ]
    txn_file.write_text(json.dumps(txns))
    monkeypatch.setattr(detect_subscriptions, "TXN_FILE", txn_file)
    monkeypatch.setattr(detect_subscriptions, "CONFIG_FILE", tmp_path / "config.json")

    res = detect_subscriptions.analyze()
    all_merchants = [r["merchant"] for grp in
                     (res["subscriptions"], res["bills"], res["emerging"]) for r in grp]
    assert "Netflix" in all_merchants   # non-AI recurring merchant still detected
    assert "ANTHROPIC" not in all_merchants


def test_anomaly_scan_returns_structured_findings(tmp_path, monkeypatch):
    # scan() powers /api/anomalies and the Trends "Findings" section. It should
    # flag a charge well above a merchant's norm (large) and a never-seen merchant.
    txn_file = tmp_path / "plaid_transactions.json"
    txns = [
        {"date": "2026-03-15", "description": "Cafe", "amount": 50.0,
         "is_income": False, "pending": False, "plaid_category": "FOOD_AND_DRINK"},
        {"date": "2026-04-15", "description": "Cafe", "amount": 50.0,
         "is_income": False, "pending": False, "plaid_category": "FOOD_AND_DRINK"},
        {"date": "2026-06-15", "description": "Cafe", "amount": 200.0,  # 4x norm -> large
         "is_income": False, "pending": False, "plaid_category": "FOOD_AND_DRINK"},
        {"date": "2026-06-20", "description": "Brand New Store", "amount": 80.0,  # never seen -> new
         "is_income": False, "pending": False, "plaid_category": "GENERAL_MERCHANDISE"},
    ]
    txn_file.write_text(json.dumps(txns))
    monkeypatch.setattr(detect_anomalies, "TXN_FILE", txn_file)

    res = detect_anomalies.scan("2026-06")
    assert res["month"] == "2026-06"
    kinds = {f["kind"] for f in res["findings"]}
    assert "large" in kinds and "new_merchant" in kinds
    # Every finding is JSON-friendly with a human detail string.
    assert all(isinstance(f["detail"], str) for f in res["findings"])
