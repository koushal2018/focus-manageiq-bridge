"""Tests for tenant config — config-driven single-tenant packaging (Spec 3)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web import tenant


def _reload(monkeypatch, path):
    monkeypatch.setattr(tenant, "CONFIG_PATH", str(path))
    tenant.config.cache_clear()


def test_defaults_when_no_config(tmp_path, monkeypatch):
    _reload(monkeypatch, tmp_path / "absent.json")
    for env in list(tenant._ENV_OVERRIDES.values()):
        monkeypatch.delenv(env, raising=False)
    c = tenant.config()
    assert c["org_name"] == "Demo Org"
    assert c["reporting_currency"] == "USD"
    assert c["fx_to_reporting"]["USD"] == 1.0
    tenant.config.cache_clear()


def test_config_file_overrides(tmp_path, monkeypatch):
    p = tmp_path / "tenant.json"
    p.write_text(json.dumps({
        "org_name": "Acme Bank", "product_name": "MoneyLens",
        "reporting_currency": "EUR",
        "fx_to_reporting": {"EUR": 1.0, "USD": 0.93},
    }))
    _reload(monkeypatch, p)
    for env in list(tenant._ENV_OVERRIDES.values()):
        monkeypatch.delenv(env, raising=False)
    c = tenant.config()
    assert c["org_name"] == "Acme Bank"
    assert c["reporting_currency"] == "EUR"
    # reporting currency is guaranteed representable at 1.0
    assert c["fx_to_reporting"]["EUR"] == 1.0
    tenant.config.cache_clear()


def test_env_overrides_win(tmp_path, monkeypatch):
    p = tmp_path / "tenant.json"
    p.write_text(json.dumps({"org_name": "File Org"}))
    _reload(monkeypatch, p)
    monkeypatch.setenv("TENANT_ORG_NAME", "Env Org")
    assert tenant.config()["org_name"] == "Env Org"
    tenant.config.cache_clear()


def test_broken_config_falls_back_not_crash(tmp_path, monkeypatch):
    p = tmp_path / "tenant.json"
    p.write_text("{ this is not json")
    _reload(monkeypatch, p)
    for env in list(tenant._ENV_OVERRIDES.values()):
        monkeypatch.delenv(env, raising=False)
    c = tenant.config()  # must not raise
    assert c["org_name"] == "Demo Org"  # fell back to defaults
    tenant.config.cache_clear()


def test_to_reporting_converts_and_rejects_unknown(tmp_path, monkeypatch):
    p = tmp_path / "tenant.json"
    p.write_text(json.dumps({
        "reporting_currency": "USD",
        "fx_to_reporting": {"USD": 1.0, "AED": 0.272},
    }))
    _reload(monkeypatch, p)
    assert tenant.to_reporting(100, "AED") == 27.2
    assert tenant.to_reporting(100, "USD") == 100.0
    import pytest
    with pytest.raises(ValueError):
        tenant.to_reporting(100, "JPY")  # unknown → raise, never silent mis-sum
    tenant.config.cache_clear()


def test_branding_derives_initials(tmp_path, monkeypatch):
    p = tmp_path / "tenant.json"
    p.write_text(json.dumps({"user_label": "Jane Doe"}))  # no explicit initials
    _reload(monkeypatch, p)
    assert tenant.branding()["user_initials"] == "JD"
    tenant.config.cache_clear()
