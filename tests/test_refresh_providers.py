"""A failed provider refresh must keep the cached balance, not clobber it.

Regression: a Shareworks scrape failure used to overwrite provider_balances.json
with an error stub, so the account vanished from Net Worth despite the log
saying "using cached data".
"""

import json

import pytest

import refresh
import providers
from providers.base import ProviderError


class _FailingProvider:
    name = "shareworks"

    def fetch(self):
        raise ProviderError("Chrome is open but not logged into Shareworks.")


class _GoodProvider:
    name = "shareworks"

    def fetch(self):
        return {"account_name": "Shareworks — ACME", "current_value": 111.0,
                "positions": [], "as_of": "2026-07-04"}


@pytest.fixture
def provider_env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"providers": [{"type": "shareworks"}]}))
    out = tmp_path / "provider_balances.json"
    monkeypatch.setattr(refresh, "CONFIG_FILE", cfg)
    monkeypatch.setattr(refresh, "PROVIDER_FILE", out)
    return out


def test_failed_provider_keeps_cached_entry(provider_env, monkeypatch):
    cached = [{"account_name": "Shareworks — ACME", "current_value": 21500.0,
               "positions": [{"symbol": "ACME", "value": 12500.0, "vested": True}],
               "as_of": "2026-07-01"}]
    provider_env.write_text(json.dumps(cached))
    monkeypatch.setattr(providers, "load_providers", lambda cfg: [_FailingProvider()])

    refresh.run_providers()

    result = json.loads(provider_env.read_text())
    assert result == cached                      # cache survives the failure
    assert not any(r.get("error") for r in result)


def test_successful_provider_replaces_cached_entry(provider_env, monkeypatch):
    provider_env.write_text(json.dumps(
        [{"account_name": "Shareworks — ACME", "current_value": 21500.0, "as_of": "2026-07-01"}]))
    monkeypatch.setattr(providers, "load_providers", lambda cfg: [_GoodProvider()])

    refresh.run_providers()

    result = json.loads(provider_env.read_text())
    assert len(result) == 1
    assert result[0]["current_value"] == 111.0   # fresh data wins, no duplicate
