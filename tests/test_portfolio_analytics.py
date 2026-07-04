from portfolio_analytics import analyze_portfolio, classify_position


def sample_accounts():
    return [
        {
            "account_name": "Chase Checking",
            "current_value": 10000,
            "total_gain_loss": 0,
            "source": "Plaid",
            "positions": [{"symbol": "CHECKING", "description": "Checking", "value": 10000}],
        },
        {
            "account_name": "CC Liability",
            "current_value": -2500,
            "total_gain_loss": 0,
            "source": "Plaid",
            "positions": [{"symbol": "CC_DEBT", "description": "Credit Card Balance", "value": -2500}],
        },
        {
            "account_name": "Roth IRA",
            "current_value": 50000,
            "total_gain_loss": 5000,
            "source": "Fidelity",
            "positions": [
                {"symbol": "VTI", "description": "Vanguard Total Stock", "value": 30000},
                {"symbol": "VXUS", "description": "Vanguard International", "value": 20000},
            ],
        },
        {
            "account_name": "401(k) — Principal",
            "current_value": 40000,
            "total_gain_loss": 0,
            "source": "Principal",
            "positions": [{"symbol": "401k", "description": "Acme Corp 401(k) — Target Date 2055", "value": 40000}],
        },
        {
            "account_name": "Shareworks — ACME",
            "current_value": 45000,
            "total_gain_loss": 0,
            "source": "Provider",
            "positions": [
                {"symbol": "ACME", "description": "Common Stock", "value": 25000, "vested": True},
                {"symbol": "ACME-RSU", "description": "Unvested RSUs", "value": 20000, "vested": False},
            ],
        },
        {
            "account_name": "Mystery Asset",
            "current_value": 15000,
            "total_gain_loss": 0,
            "source": "Manual",
            "positions": [{"symbol": "UNKNOWN", "description": "Unclassified asset", "value": 15000}],
        },
    ]


def test_analyze_portfolio_totals_and_classes():
    analytics = analyze_portfolio(sample_accounts())
    totals = analytics["totals"]
    assert totals["net_worth"] == 157500
    assert totals["gross_assets"] == 160000
    assert totals["liabilities"] == -2500
    assert totals["cash"] == 10000
    assert totals["retirement"] == 90000
    assert totals["employer_equity"] == 45000
    assert totals["unvested_equity"] == 20000
    assert totals["vested_equity"] == 25000


def test_alerts_for_concentration_and_unknowns():
    analytics = analyze_portfolio(sample_accounts())
    alerts = analytics["alerts"]
    assert any(a["type"] == "employer_equity" for a in alerts)
    assert any(a["type"] == "single_position" and a["label"] in {"VTI", "ACME"} for a in alerts)
    assert analytics["data_quality"]["unknown_value"] == 15000
    assert analytics["data_quality"]["unknown_positions"]


def test_liabilities_excluded_from_investable_denominator():
    analytics = analyze_portfolio(sample_accounts())
    gross_investable = analytics["totals"]["gross_investable_assets"]
    assert gross_investable == 160000
    cc = analytics["exposures"]["by_symbol"]["CC_DEBT"]
    assert cc == -2500


def test_threshold_config_overrides_defaults():
    cfg = {"concentration": {"thresholds": {"employer_equity_warning": 0.50, "employer_equity_critical": 0.75}}}
    analytics = analyze_portfolio(sample_accounts(), cfg)
    assert not any(a["type"] == "employer_equity" for a in analytics["alerts"])


def test_classification_heuristics():
    account = {"account_name": "Shareworks — ACME", "source": "Provider", "positions": []}
    pos = {"symbol": "ACME-RSU", "description": "Unvested RSUs", "value": 1000, "vested": False}
    meta = classify_position(account, pos)
    assert meta["asset_class"] == "Employer Equity"
    assert meta["is_unvested"] is True
