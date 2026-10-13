"""Position-related market news for finance-viz.

Uses public Yahoo Finance RSS feeds, keyed off the holdings already refreshed into
``~/.finance-viz``. This keeps news automatic without adding another paid API key.
"""

from __future__ import annotations

import html
import json
import os
import stat
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # nosemgrep: python.lang.security.use-defused-xml.use-defused-xml -- entity declarations are rejected before parsing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


BASE_DIR = Path(os.environ.get("FINANCE_VIZ_DIR", Path.home() / ".finance-viz"))
PLAID_HOLDINGS_FILE = BASE_DIR / "plaid_holdings.json"
PROVIDER_FILE = BASE_DIR / "provider_balances.json"
NEWS_FILE = BASE_DIR / "position_news.json"

EXCLUDED_SYMBOLS = {
    "", "UNKNOWN", "ACCT", "CASH", "CORE", "SPAXX", "HYSA", "CHECKING", "SAVINGS",
    "MONEY_MARKET", "CC_DEBT", "401K", "401_K", "RSU",
}

YAHOO_SYMBOL_OVERRIDES = {
    "BRKB": "BRK-B",
    "BRK.B": "BRK-B",
}


def _read_json(path: Path, fallback):
    try:
        if path.exists():
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return fallback


def _save_json(path: Path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    tmp.replace(path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def normalize_symbol(symbol: str | None) -> str:
    sym = (symbol or "").replace("*", "").strip().upper()
    return YAHOO_SYMBOL_OVERRIDES.get(sym, sym)


def collect_position_symbols(max_symbols: int = 14):
    """Return symbols ranked by current holding value."""
    by_symbol: dict[str, dict] = {}

    plaid_holdings = _read_json(PLAID_HOLDINGS_FILE, {})
    for label, item in plaid_holdings.items():
        if not isinstance(item, dict) or "error" in item:
            continue
        for account in item.get("accounts", []):
            acct_name = account.get("name") or label
            for pos in account.get("positions", []):
                sym = normalize_symbol(pos.get("symbol"))
                if sym in EXCLUDED_SYMBOLS or sym.endswith("-RSU") or pos.get("is_cash_equivalent"):
                    continue
                value = float(pos.get("value") or 0)
                if value <= 0:
                    continue
                rec = by_symbol.setdefault(sym, {
                    "symbol": sym,
                    "value": 0.0,
                    "accounts": set(),
                    "description": pos.get("description") or sym,
                })
                rec["value"] += value
                rec["accounts"].add(acct_name)

    providers = _read_json(PROVIDER_FILE, [])
    if isinstance(providers, list):
        for provider in providers:
            if not isinstance(provider, dict) or "error" in provider:
                continue
            acct_name = provider.get("account_name") or "Provider"
            for pos in provider.get("positions", []):
                sym = normalize_symbol(pos.get("symbol"))
                if sym in EXCLUDED_SYMBOLS or sym.endswith("-RSU"):
                    continue
                value = float(pos.get("value") or 0)
                if value <= 0:
                    continue
                rec = by_symbol.setdefault(sym, {
                    "symbol": sym,
                    "value": 0.0,
                    "accounts": set(),
                    "description": pos.get("description") or sym,
                })
                rec["value"] += value
                rec["accounts"].add(acct_name)

    ranked = sorted(by_symbol.values(), key=lambda r: r["value"], reverse=True)
    out = []
    for rec in ranked[:max_symbols]:
        out.append({
            "symbol": rec["symbol"],
            "value": round(rec["value"], 2),
            "accounts": sorted(rec["accounts"]),
            "description": rec["description"],
        })
    return out


def _parse_pub_date(value):
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def fetch_yahoo_rss(symbol: str, timeout: int = 8):
    url = "https://feeds.finance.yahoo.com/rss/2.0/headline?" + urllib.parse.urlencode({
        "s": symbol,
        "region": "US",
        "lang": "en-US",
    })
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "finance-viz/1.0 (+local dashboard)",
            "Accept": "application/rss+xml, application/xml, text/xml",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- fixed host or localhost; scheme is not user-controlled
        raw = resp.read()

    # Entity declarations are the only route to entity-expansion attacks, and
    # real RSS does not declare any.
    if b"<!ENTITY" in raw.upper():
        raise ET.ParseError("feed declares XML entities")
    root = ET.fromstring(raw)
    stories = []
    for item in root.findall("./channel/item"):
        title = html.unescape((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        description = html.unescape((item.findtext("description") or "").strip())
        pub_date = _parse_pub_date(item.findtext("pubDate"))
        source = ""
        source_el = item.find("{http://search.yahoo.com/mrss/}credit")
        if source_el is not None and source_el.text:
            source = source_el.text.strip()
        stories.append({
            "symbol": symbol,
            "title": title,
            "link": link,
            "summary": description,
            "published_at": pub_date,
            "source": source or "Yahoo Finance",
        })
    return stories


def refresh_position_news(max_symbols: int = 14, per_symbol: int = 3, total_limit: int = 28, write: bool = True):
    symbols = collect_position_symbols(max_symbols=max_symbols)
    stories = []
    errors = []
    seen = set()
    value_by_symbol = {s["symbol"]: s["value"] for s in symbols}

    for item in symbols:
        symbol = item["symbol"]
        try:
            fetched = fetch_yahoo_rss(symbol)
            for story in fetched[:per_symbol]:
                key = story["link"] or story["title"]
                if key in seen:
                    continue
                seen.add(key)
                story["position_value"] = item["value"]
                story["accounts"] = item["accounts"]
                stories.append(story)
            time.sleep(0.15)
        except Exception as e:
            errors.append({"symbol": symbol, "error": str(e)})

    def sort_key(story):
        published = story.get("published_at") or ""
        return (published, value_by_symbol.get(story.get("symbol"), 0))

    stories.sort(key=sort_key, reverse=True)
    payload = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "stories": stories[:total_limit],
        "errors": errors,
        "source": "Yahoo Finance RSS",
    }
    if write:
        _save_json(NEWS_FILE, payload)
    return payload


def load_position_news():
    return _read_json(NEWS_FILE, {
        "as_of": None,
        "symbols": [],
        "stories": [],
        "errors": [],
        "source": "Yahoo Finance RSS",
    })


if __name__ == "__main__":
    data = refresh_position_news()
    print(f"Fetched {len(data['stories'])} stories for {len(data['symbols'])} symbols")
    for story in data["stories"][:10]:
        print(f"{story['symbol']}: {story['title']}")
