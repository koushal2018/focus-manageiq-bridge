"""Tenant configuration — the config-driven, single-tenant-per-deploy surface.

The product is reused by cloning the repo and dropping in ONE config file:
`config/tenant.json`. Everything customer-specific that was hardcoded for ENBD
(branding, product name, user label, reporting currency + FX rates) is read
from here, so onboarding a new customer is configuration, not a code fork.

Resolution order (later wins):
  1. built-in DEFAULTS (below) — a generic, un-branded tenant
  2. config/tenant.json if present (the per-deploy file a customer edits)
  3. env overrides for the few fields a deploy pipeline sets without a file
     (TENANT_ORG_NAME, TENANT_PRODUCT_NAME, TENANT_REPORTING_CURRENCY)

JSON, not YAML, deliberately: stdlib only — no new dependency, so a customer
clone-and-deploys without pip-installing a parser. FX rates live here too so a
non-USD reporting currency is a config change, not a code change (the loader and
join read get_fx_to_reporting()).
"""
from __future__ import annotations

import json
import os
import functools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.environ.get("TENANT_CONFIG", os.path.join(ROOT, "config", "tenant.json"))

# A generic, obviously-unbranded default tenant. A fresh clone runs with this
# until the customer drops in their own config/tenant.json.
DEFAULTS: dict = {
    "org_name": "Demo Org",
    "product_name": "CloudLens FinOps",
    "user_label": "Demo User",
    # user_initials intentionally NOT defaulted — branding() derives it from
    # user_label unless the customer sets it explicitly, so a tenant that sets
    # only user_label gets sensible initials instead of a stale default.
    "environment_note": "synthetic data — not a real tenant",
    # Reporting currency the dashboard sums in. FX rates convert each source
    # currency INTO it. Must include reporting_currency itself at rate 1.0.
    "reporting_currency": "USD",
    "fx_to_reporting": {
        "USD": 1.0,
        "AED": 1.0 / 3.6725,   # AED pegged; a customer edits these for their book
        "EUR": 1.08,
        "GBP": 1.27,
    },
}

_ENV_OVERRIDES = {
    "org_name": "TENANT_ORG_NAME",
    "product_name": "TENANT_PRODUCT_NAME",
    "user_label": "TENANT_USER_LABEL",
    "reporting_currency": "TENANT_REPORTING_CURRENCY",
}


@functools.lru_cache(maxsize=1)
def config() -> dict:
    """The resolved tenant config (cached). Call config.cache_clear() in tests."""
    cfg = dict(DEFAULTS)
    cfg["fx_to_reporting"] = dict(DEFAULTS["fx_to_reporting"])
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                user = json.load(f)
            # shallow-merge top level; fx map replaced wholesale if provided
            for k, v in user.items():
                cfg[k] = v
        except (ValueError, OSError) as e:
            # A broken config must not crash the app — fall back to defaults,
            # but make the breakage visible.
            print(f"[tenant] WARNING: could not read {CONFIG_PATH}: {e}; using defaults")
    for key, env in _ENV_OVERRIDES.items():
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    # Invariant: reporting_currency must be representable at rate 1.0.
    rc = (cfg.get("reporting_currency") or "USD").upper()
    cfg["reporting_currency"] = rc
    cfg["fx_to_reporting"].setdefault(rc, 1.0)
    return cfg


def get(key: str):
    return config().get(key)


def reporting_currency() -> str:
    return config()["reporting_currency"]


def fx_to_reporting() -> dict:
    return config()["fx_to_reporting"]


def to_reporting(amount: float, currency: str) -> float:
    """Convert `amount` in `currency` into the tenant's reporting currency.
    Raises on an unknown currency rather than silently mis-summing (the H-1 /
    B-6/B-7 currency-integrity rule applies per-tenant too)."""
    rate = fx_to_reporting().get((currency or "").upper())
    if rate is None:
        raise ValueError(f"no FX rate to {reporting_currency()} for currency {currency!r}")
    return round(float(amount) * rate, 6)


def branding() -> dict:
    """The subset the templates need (passed into every TemplateResponse)."""
    c = config()
    return {
        "org_name": c["org_name"],
        "product_name": c["product_name"],
        "user_label": c["user_label"],
        "user_initials": c.get("user_initials") or "".join(
            w[0] for w in c["user_label"].split()[:2]).upper(),
        "environment_note": c.get("environment_note", ""),
        "reporting_currency": c["reporting_currency"],
    }
