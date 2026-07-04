"""Portfolio and concentration analytics for finance-viz.

Consumes the existing account snapshot shape returned by ``load_networth()`` and
adds a conservative exposure layer. It intentionally uses local provider
metadata/heuristics only: no market-data or tax-lot dependency.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from finance_config import load_finance_config

DEFAULT_THRESHOLDS = {
    "single_position_warning": 0.15,
    "single_position_critical": 0.25,
    "single_account_warning": 0.35,
    "single_account_critical": 0.50,
    "employer_equity_warning": 0.20,
    "employer_equity_critical": 0.35,
    "unclassified_warning": 0.10,
}

CASH_SYMBOLS = {"CHECKING", "SAVINGS", "MONEY_MARKET", "CD", "HYSA", "CASH"}
LIABILITY_SYMBOLS = {"CC_DEBT", "CREDIT_CARD", "LOAN"}
# Extend via "employer_symbols" / "employer_keywords" in config.json — your
# employer ticker is personal config, not code.
_cfg = load_finance_config()
EMPLOYER_SYMBOLS = {"RSU", "ESPP"} | {
    str(s).strip().upper().replace(" ", "_") for s in _cfg.get("employer_symbols", [])
}
_EMPLOYER_KEYWORDS = tuple(str(k).lower() for k in _cfg.get("employer_keywords", []))
ACCOUNT_PROXY_SYMBOLS = {
    "BROKERAGE", "INVESTMENT", "RETIREMENT", "IRA", "ROTH", "401K", "401_K",
    "CHECKING", "SAVINGS", "MONEY_MARKET", "CD", "HYSA", "ACCT",
}


def _round_money(value):
    return round(float(value or 0), 2)


def _round_pct(value):
    return round(float(value or 0), 4)


def _pct(value, denom):
    return _round_pct((float(value or 0) / denom) if denom else 0.0)


def _normalize_symbol(symbol):
    return (symbol or "UNKNOWN").strip().upper().replace(" ", "_") or "UNKNOWN"


def _text_blob(account, position):
    return " ".join([
        str(account.get("account_name", "")),
        str(account.get("account_number", "")),
        str(account.get("source", "")),
        str(position.get("symbol", "")),
        str(position.get("description", "")),
    ]).lower()


def _is_account_proxy(account, position):
    source = (account.get("source") or "").lower()
    symbol = _normalize_symbol(position.get("symbol"))
    positions = account.get("positions") or []
    desc = (position.get("description") or "").lower()
    if source == "plaid" and len(positions) == 1:
        return True
    if symbol in ACCOUNT_PROXY_SYMBOLS and len(positions) == 1:
        return True
    if desc in {"credit card balance", "high-yield savings"}:
        return True
    return False


def classify_position(account, position):
    """Return conservative metadata for one position/account line."""
    text = _text_blob(account, position)
    symbol = _normalize_symbol(position.get("symbol"))
    value = float(position.get("value") or 0)
    source = account.get("source", "")

    vested = position.get("vested")
    if vested is None:
        vested = "unvested" not in text and not symbol.endswith("RSU")

    if value < 0 or symbol in LIABILITY_SYMBOLS or "liability" in text or "credit card" in text:
        asset_class = "Liability"
    elif "401" in text or "ira" in text or "principal" in text or "retirement" in text:
        asset_class = "Retirement"
    elif (symbol in EMPLOYER_SYMBOLS or "shareworks" in text or "rsu" in text
          or any(k in text for k in _EMPLOYER_KEYWORDS)):
        asset_class = "Employer Equity"
    elif symbol in CASH_SYMBOLS or "checking" in text or "savings" in text or "hysa" in text or "money market" in text:
        asset_class = "Cash"
    elif symbol == "UNKNOWN" or symbol == "ACCT":
        asset_class = "Unclassified"
    else:
        asset_class = "Marketable Securities"

    return {
        "symbol": symbol,
        "asset_class": asset_class,
        "vested": bool(vested),
        "source": source,
        "is_liability": asset_class == "Liability" or value < 0,
        "is_employer_equity": asset_class == "Employer Equity",
        "is_unvested": not bool(vested),
        "is_account_proxy": _is_account_proxy(account, position),
    }


def _load_thresholds(config=None):
    thresholds = DEFAULT_THRESHOLDS.copy()
    if config:
        custom = (config.get("concentration") or {}).get("thresholds") or {}
        for key, value in custom.items():
            if key in thresholds:
                thresholds[key] = float(value)
    return thresholds


def _bucket_add(bucket, key, value):
    bucket[key] += float(value or 0)


def _sorted_money_dict(bucket):
    return {
        key: _round_money(value)
        for key, value in sorted(bucket.items(), key=lambda item: abs(item[1]), reverse=True)
        if round(value, 2) != 0
    }


def _alert(level, kind, label, value, pct, threshold, message):
    return {
        "level": level,
        "type": kind,
        "label": label,
        "value": _round_money(value),
        "pct": _round_pct(pct),
        "threshold": _round_pct(threshold),
        "message": message,
    }


def analyze_portfolio(accounts, config=None):
    """Compute exposure, concentration, and data-quality analytics."""
    accounts = deepcopy(accounts or [])
    thresholds = _load_thresholds(config)

    net_worth = sum(float(a.get("current_value") or 0) for a in accounts)
    liabilities = sum(float(a.get("current_value") or 0) for a in accounts if float(a.get("current_value") or 0) < 0)
    gross_assets = sum(max(float(a.get("current_value") or 0), 0.0) for a in accounts)

    by_account = defaultdict(float)
    by_source = defaultdict(float)
    by_asset_class = defaultdict(float)
    by_vesting = defaultdict(float)
    by_symbol = defaultdict(float)
    top_positions = []
    unknown_positions = []
    granularity_notes = []

    for account in accounts:
        account_name = account.get("account_name") or "Unknown Account"
        source = account.get("source") or "Unknown"
        acct_value = float(account.get("current_value") or 0)
        _bucket_add(by_account, account_name, acct_value)
        _bucket_add(by_source, source, acct_value)

        for position in account.get("positions") or []:
            value = float(position.get("value") or 0)
            meta = classify_position(account, position)
            symbol = meta["symbol"]
            asset_class = meta["asset_class"]

            _bucket_add(by_asset_class, asset_class, value)
            _bucket_add(by_symbol, symbol, value)
            if meta["is_unvested"]:
                _bucket_add(by_vesting, "Unvested", value)
            elif not meta["is_liability"]:
                _bucket_add(by_vesting, "Vested/Available", value)

            enriched = {
                "account_name": account_name,
                "symbol": symbol,
                "description": position.get("description", ""),
                "value": _round_money(value),
                "asset_class": asset_class,
                "source": source,
                "vested": meta["vested"],
                "is_account_proxy": meta["is_account_proxy"],
            }
            if not meta["is_liability"] and value > 0:
                top_positions.append(enriched)
            if asset_class == "Unclassified" and value > 0:
                unknown_positions.append(enriched)
            if meta["is_account_proxy"] and value > 0:
                granularity_notes.append(
                    f"{account_name} is represented by account-level data; true underlying holdings are not available."
                )

    gross_investable_assets = sum(
        value for asset_class, value in by_asset_class.items()
        if asset_class != "Liability" and value > 0
    )

    cash = by_asset_class.get("Cash", 0.0)
    retirement = by_asset_class.get("Retirement", 0.0)
    employer_equity = by_asset_class.get("Employer Equity", 0.0)
    unvested_equity = by_vesting.get("Unvested", 0.0)
    vested_equity = max(employer_equity - unvested_equity, 0.0)
    unknown_value = by_asset_class.get("Unclassified", 0.0)

    denom = gross_investable_assets or gross_assets or abs(net_worth) or 0.0
    for p in top_positions:
        p["pct"] = _pct(p["value"], denom)
    top_positions.sort(key=lambda p: abs(p["value"]), reverse=True)

    alerts = []
    for p in top_positions[:10]:
        if p["is_account_proxy"]:
            continue
        pct = p["pct"]
        if pct >= thresholds["single_position_critical"]:
            alerts.append(_alert("critical", "single_position", p["symbol"], p["value"], pct, thresholds["single_position_critical"], f"{p['symbol']} exceeds critical single-position threshold."))
        elif pct >= thresholds["single_position_warning"]:
            alerts.append(_alert("warning", "single_position", p["symbol"], p["value"], pct, thresholds["single_position_warning"], f"{p['symbol']} exceeds warning single-position threshold."))

    for account_name, value in by_account.items():
        if value <= 0:
            continue
        pct = _pct(value, denom)
        if pct >= thresholds["single_account_critical"]:
            alerts.append(_alert("critical", "single_account", account_name, value, pct, thresholds["single_account_critical"], f"{account_name} exceeds critical account concentration threshold."))
        elif pct >= thresholds["single_account_warning"]:
            alerts.append(_alert("warning", "single_account", account_name, value, pct, thresholds["single_account_warning"], f"{account_name} exceeds warning account concentration threshold."))

    employer_pct = _pct(employer_equity, denom)
    if employer_pct >= thresholds["employer_equity_critical"]:
        alerts.append(_alert("critical", "employer_equity", "Employer equity concentration", employer_equity, employer_pct, thresholds["employer_equity_critical"], "Employer equity exceeds critical threshold."))
    elif employer_pct >= thresholds["employer_equity_warning"]:
        alerts.append(_alert("warning", "employer_equity", "Employer equity concentration", employer_equity, employer_pct, thresholds["employer_equity_warning"], "Employer equity exceeds warning threshold."))

    unknown_pct = _pct(unknown_value, denom)
    if unknown_pct >= thresholds["unclassified_warning"]:
        alerts.append(_alert("warning", "unclassified", "Unclassified exposure", unknown_value, unknown_pct, thresholds["unclassified_warning"], "Unclassified assets exceed threshold; classification may need refinement."))

    level_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: (level_order.get(a["level"], 9), -abs(a["pct"])))

    return {
        "totals": {
            "net_worth": _round_money(net_worth),
            "gross_assets": _round_money(gross_assets),
            "gross_investable_assets": _round_money(gross_investable_assets),
            "liabilities": _round_money(liabilities),
            "cash": _round_money(cash),
            "retirement": _round_money(retirement),
            "employer_equity": _round_money(employer_equity),
            "vested_equity": _round_money(vested_equity),
            "unvested_equity": _round_money(unvested_equity),
        },
        "exposures": {
            "by_account": _sorted_money_dict(by_account),
            "by_source": _sorted_money_dict(by_source),
            "by_asset_class": _sorted_money_dict(by_asset_class),
            "by_vesting": _sorted_money_dict(by_vesting),
            "by_symbol": _sorted_money_dict(by_symbol),
        },
        "top_positions": top_positions[:25],
        "alerts": alerts,
        "data_quality": {
            "unknown_positions": unknown_positions,
            "unknown_value": _round_money(unknown_value),
            "unknown_pct": unknown_pct,
            "granularity_notes": sorted(set(granularity_notes)),
        },
        "thresholds": thresholds,
    }
