import app as finance_app


def test_api_networth_includes_analytics(monkeypatch):
    accounts = [
        {
            "account_name": "Shareworks — ACME",
            "current_value": 50000,
            "total_gain_loss": 0,
            "source": "Provider",
            "positions": [
                {"symbol": "ACME", "description": "Common Stock", "value": 30000, "vested": True},
                {"symbol": "ACME-RSU", "description": "Unvested RSUs", "value": 20000, "vested": False},
            ],
        },
        {
            "account_name": "CC Liability",
            "current_value": -1000,
            "total_gain_loss": 0,
            "source": "Plaid",
            "positions": [{"symbol": "CC_DEBT", "description": "Credit Card Balance", "value": -1000}],
        },
    ]
    monkeypatch.setattr(finance_app, "load_networth", lambda: accounts)
    monkeypatch.setattr(finance_app, "load_finance_config", lambda: {})
    client = finance_app.app.test_client()
    resp = client.get("/api/networth")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["accounts"] == accounts
    assert data["total"] == 49000
    assert "analytics" in data
    assert data["analytics"]["totals"]["employer_equity"] == 50000
    assert data["analytics"]["totals"]["liabilities"] == -1000
    assert data["analytics"]["alerts"]
