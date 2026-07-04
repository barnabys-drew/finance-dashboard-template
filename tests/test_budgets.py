"""Tests for the per-bucket budgets passthrough on /api/data.

Budget *pace* math (proration, green/amber/red) lives in the frontend so it can
react to the client-side month filter — these tests cover the server-side
contract: config.json `budgets` is validated (real bucket names, positive
numbers) and echoed through; anything else is dropped, never an error.
"""

import json

import app as finance_app


def _txn(date, desc, amount, category, is_income=False, plaid_category=""):
    return {"date": date, "description": desc, "amount": amount, "category": category,
            "is_income": is_income, "is_savings": False, "payment_channel": "online",
            "plaid_category": plaid_category}


def _get_budgets(tmp_path, monkeypatch, cfg):
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    monkeypatch.setattr(finance_app, "PLAID_DIR", tmp_path)
    monkeypatch.setattr(finance_app, "load_transactions", lambda: [
        _txn("2026-06-01", "SOME STORE", 10.0, "Shopping"),
    ])
    return finance_app.app.test_client().get("/api/data").get_json()["budgets"]


def test_budgets_passthrough_drops_invalid_entries(tmp_path, monkeypatch):
    budgets = _get_budgets(tmp_path, monkeypatch, {"budgets": {
        "dining": 300,        # valid
        "household": 2750.5,  # valid float
        "bogus": 100,         # not a bucket -> dropped
        "food": -5,           # non-positive -> dropped
        "subs": "x",          # non-numeric -> dropped
        "pet": 0,             # zero -> dropped
        "ai": True,           # bool is not a budget -> dropped
    }})
    assert budgets == {"dining": 300.0, "household": 2750.5}


def test_budgets_missing_key_returns_empty(tmp_path, monkeypatch):
    assert _get_budgets(tmp_path, monkeypatch, {}) == {}
