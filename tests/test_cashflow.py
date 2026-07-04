"""Tests for forecast_cashflow: payroll stream detection, bill scheduling,
discretionary burn, and the balance simulation. All dates are injected via
analyze(today=...) so nothing depends on the wall clock."""

import json
from datetime import date

import pytest

import forecast_cashflow


TODAY = date(2026, 7, 1)


def _txn(d, desc, amount, plaid_category="", is_income=False, pending=False):
    return {"date": d, "description": desc, "amount": amount,
            "plaid_category": plaid_category, "is_income": is_income,
            "pending": pending}


def _checking(balance=5000.0, name="Chase Checking"):
    return [{"account_name": name, "current_value": balance,
             "total_gain_loss": 0.0}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point the module at tmp files; return a setup(txns, cfg, subs, accounts)
    helper so each test declares only what it cares about."""
    txn_file = tmp_path / "plaid_transactions.json"
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(forecast_cashflow, "TXN_FILE", txn_file)
    monkeypatch.setattr(forecast_cashflow, "CONFIG_FILE", cfg_file)

    def setup(txns=(), cfg=None, subs=None, accounts=None):
        txn_file.write_text(json.dumps(list(txns)))
        cfg_file.write_text(json.dumps(cfg or {}))
        monkeypatch.setattr(forecast_cashflow, "load_networth",
                            lambda: accounts if accounts is not None else _checking())
        monkeypatch.setattr(forecast_cashflow.detect_subscriptions, "analyze",
                            lambda: subs or {"subscriptions": [], "bills": [],
                                             "lapsed": [], "emerging": []})
    return setup


def test_payroll_cadence_and_median_amount(env):
    # 6 biweekly paydays; one bonus month must not shift the median amount.
    txns = [
        _txn("2026-04-24", "ACME PAYROLL", 2000.0, is_income=True),
        _txn("2026-05-08", "ACME PAYROLL", 2000.0, is_income=True),
        _txn("2026-05-22", "ACME PAYROLL", 2000.0, is_income=True),
        _txn("2026-06-05", "ACME PAYROLL", 9000.0, is_income=True),  # bonus
        _txn("2026-06-19", "ACME PAYROLL", 2000.0, is_income=True),
        _txn("2026-07-03", "ACME PAYROLL", 2000.0, is_income=True),
    ]
    env(txns=txns)
    res = forecast_cashflow.analyze(horizon_days=30, today=date(2026, 7, 4))
    a = res["assumptions"]
    assert a["payroll_cadence_days"] == 14
    assert a["payroll_amount"] == 2000.0
    # Next paydays step 14 days from the last one.
    assert [p["date"] for p in res["paychecks"]] == ["2026-07-17", "2026-07-31"]


def test_multiple_payroll_streams_projected_separately(env):
    # A semimonthly salary + a weekly one: pooling the dates would give a
    # nonsense ~5-day cadence; per-stream grouping must keep them apart.
    txns = (
        [_txn(d, "BIGCO PAYROLL", 4000.0, is_income=True)
         for d in ("2026-05-15", "2026-05-31", "2026-06-15", "2026-06-30")]
        + [_txn(d, "SMALLCO PAYROLL", 1000.0, is_income=True)
           for d in ("2026-06-08", "2026-06-15", "2026-06-22", "2026-06-29")]
    )
    env(txns=txns)
    res = forecast_cashflow.analyze(horizon_days=30, today=TODAY)
    streams = {s["name"]: s for s in res["assumptions"]["payroll_streams"]}
    assert streams["BIGCO"]["cadence_days"] in (15, 16)
    assert streams["SMALLCO"]["cadence_days"] == 7
    # Headline assumption reflects the biggest stream.
    assert res["assumptions"]["payroll_amount"] == 4000.0


def test_no_payroll_history_still_forecasts(env):
    env(txns=[_txn("2026-06-15", "STORE", 50.0)])
    res = forecast_cashflow.analyze(horizon_days=30, today=TODAY)
    assert res["available"] is True
    assert res["paychecks"] == []
    assert res["assumptions"]["payroll_cadence_days"] is None


def test_monthly_bill_appears_twice_in_60d_horizon(env):
    subs = {"subscriptions": [], "lapsed": [], "emerging": [],
            "bills": [{"merchant": "Verizon", "cadence": "monthly",
                       "latest": 116.91, "last_date": "2026-06-11",
                       "bucket": "electricity", "category": "Utilities"}]}
    env(subs=subs)
    res = forecast_cashflow.analyze(horizon_days=60, today=TODAY)
    dates = [b["date"] for b in res["upcoming_bills"]]
    assert dates == ["2026-07-11", "2026-08-11"]


def test_annual_bill_included_only_when_due(env):
    def annual(last):
        return {"subscriptions": [], "lapsed": [], "emerging": [],
                "bills": [{"merchant": "AAA", "cadence": "annual",
                           "latest": 120.0, "last_date": last,
                           "bucket": "subs", "category": "Subscriptions"}]}
    env(subs=annual("2025-07-20"))  # due ~July 20 — inside 60d horizon
    res = forecast_cashflow.analyze(horizon_days=60, today=TODAY)
    assert [b["date"] for b in res["upcoming_bills"]] == ["2026-07-20"]

    env(subs=annual("2026-04-01"))  # renewed 3 months ago — not due
    res = forecast_cashflow.analyze(horizon_days=60, today=TODAY)
    assert res["upcoming_bills"] == []


def test_overdue_bill_scheduled_tomorrow_not_dropped(env):
    subs = {"subscriptions": [], "lapsed": [], "emerging": [],
            "bills": [{"merchant": "Verizon", "cadence": "monthly",
                       "latest": 100.0, "last_date": "2026-05-25",
                       "bucket": "electricity", "category": "Utilities"}]}
    env(subs=subs)
    res = forecast_cashflow.analyze(horizon_days=30, today=TODAY)
    # Expected 2026-06-25 already passed -> tomorrow; next cycle still lands.
    assert [b["date"] for b in res["upcoming_bills"]] == ["2026-07-02", "2026-07-25"]


def test_lapsed_and_emerging_excluded_from_bills(env):
    subs = {"subscriptions": [], "bills": [],
            "lapsed": [{"merchant": "Old Gym", "cadence": "monthly",
                        "latest": 50.0, "last_date": "2026-03-01",
                        "bucket": "subs", "category": "Subscriptions"}],
            "emerging": [{"merchant": "New Thing", "cadence": "monthly",
                          "latest": 9.99, "last_date": "2026-06-20",
                          "bucket": "subs", "category": "Subscriptions"}]}
    env(subs=subs)
    res = forecast_cashflow.analyze(horizon_days=60, today=TODAY)
    assert res["upcoming_bills"] == []


def test_burn_uses_complete_months_only(env):
    # April+May are complete ($30/day and $10/day); the 1-day June edge must
    # not dilute the average. Recurring merchants and transfers are excluded.
    txns = []
    for day in range(1, 31):
        txns.append(_txn(f"2026-04-{day:02d}", "COFFEE", 30.0))
    for day in range(1, 32):
        txns.append(_txn(f"2026-05-{day:02d}", "COFFEE", 10.0))
    txns.append(_txn("2026-06-01", "COFFEE", 500.0))                 # partial month
    txns.append(_txn("2026-05-10", "Verizon", 116.91))               # recurring
    txns.append(_txn("2026-05-11", "ALLY BANK TRANSFER", 2000.0))    # skip prefix
    txns.append(_txn("2026-05-12", "CC PAYOFF", 900.0, plaid_category="TRANSFER_OUT"))
    subs = {"subscriptions": [], "lapsed": [], "emerging": [],
            "bills": [{"merchant": "Verizon", "cadence": "monthly",
                       "latest": 116.91, "last_date": "2026-05-10",
                       "bucket": "electricity", "category": "Utilities"}]}
    env(txns=txns, subs=subs)
    res = forecast_cashflow.analyze(horizon_days=30, today=TODAY)
    a = res["assumptions"]
    assert a["burn_months"] == ["2026-04", "2026-05"]
    # (30*30 + 31*10) / 61 days = 19.84
    assert a["daily_burn"] == pytest.approx(19.84, abs=0.01)


def test_no_complete_months_falls_back_to_window(env):
    txns = [_txn("2026-06-20", "COFFEE", 10.0), _txn("2026-06-29", "COFFEE", 10.0)]
    env(txns=txns)
    res = forecast_cashflow.analyze(horizon_days=30, today=TODAY)
    a = res["assumptions"]
    assert a["burn_months"] == []
    assert a["daily_burn"] == pytest.approx(20.0 / 10, abs=0.01)  # $20 over 10 days


def test_min_balance_safe_to_save_and_shortfall(env):
    # No income, no bills, $100/day burn from a complete month, $4000 start:
    # balance falls $100/day for 14 days -> min = 4000 - 1400 = 2600.
    txns = [_txn(f"2026-06-{d:02d}", "SPEND", 100.0) for d in range(1, 31)]
    env(txns=txns, cfg={"cashflow": {"buffer": 2000}},
        accounts=_checking(4000.0))
    res = forecast_cashflow.analyze(horizon_days=14, today=TODAY)
    assert res["min_balance"] == pytest.approx(2600.0)
    assert res["min_date"] == "2026-07-15"
    assert res["safe_to_save"] == pytest.approx(600.0)
    assert res["shortfall"] is False

    # Longer horizon dips below the buffer -> shortfall, nothing safe to move.
    res = forecast_cashflow.analyze(horizon_days=30, today=TODAY)
    assert res["min_balance"] == pytest.approx(1000.0)
    assert res["shortfall"] is True
    assert res["safe_to_save"] == 0.0


def test_missing_checking_account_unavailable(env):
    env(accounts=[])
    res = forecast_cashflow.analyze(today=TODAY)
    assert res["available"] is False


def test_route_returns_unavailable_on_error(monkeypatch):
    import app as finance_app
    def boom():
        raise RuntimeError("nope")
    monkeypatch.setattr(finance_app, "analyze_cashflow", boom)
    data = finance_app.app.test_client().get("/api/cashflow").get_json()
    assert data == {"available": False}
