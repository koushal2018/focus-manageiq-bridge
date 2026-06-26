"""Source registry — the list of connected data sources.

In production this is a DB table written by the 'Connect a source' admin UI.
For the PoC it's a JSON file (out/connectors/sources.json) seeded with the
three synthetic generators' outputs, so the dispatcher has something to run
and the connect-and-run flow is demonstrable end to end.

Each entry is a SourceConfig. The point: onboarding a new cloud account is
adding one of these rows + a credential — never a code change.
"""
from __future__ import annotations

import json
import os

from connectors.contract import SourceConfig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_PATH = os.environ.get(
    "CONNECTOR_REGISTRY",
    os.path.join(ROOT, "out", "connectors", "sources.json"),
)


# The PoC seed: the three synthetic exports the generators produce. In
# production these rows come from the admin UI; here we ship them so a fresh
# `connectors.dispatcher` run has sources to process.
POC_SEED: list[SourceConfig] = [
    SourceConfig(
        source_id="aws-payer-demo",
        source_type="aws-cur",
        display_name="AWS — DEMO payer 999900001111",
        location="out/generators/aws_cur.csv",
        credential_ref="demo:no-credential-needed-synthetic",
        schedule="daily",
    ),
    SourceConfig(
        source_id="azure-sub-demo",
        source_type="azure-export",
        display_name="Azure — DEMO subscription",
        location="out/generators/azure_cost.csv",
        credential_ref="demo:no-credential-needed-synthetic",
        schedule="daily",
    ),
    SourceConfig(
        source_id="oci-tenancy-demo",
        source_type="oci-usage",
        display_name="OCI — DEMO tenancy",
        location="out/generators/oci_usage.csv",
        credential_ref="demo:no-credential-needed-synthetic",
        schedule="daily",
    ),
]


def _to_dict(c: SourceConfig) -> dict:
    return {
        "source_id": c.source_id, "source_type": c.source_type,
        "display_name": c.display_name, "location": c.location,
        "credential_ref": c.credential_ref, "schedule": c.schedule,
        "enabled": c.enabled,
    }


def _from_dict(d: dict) -> SourceConfig:
    return SourceConfig(
        source_id=d["source_id"], source_type=d["source_type"],
        display_name=d["display_name"], location=d["location"],
        credential_ref=d["credential_ref"], schedule=d.get("schedule", "daily"),
        enabled=d.get("enabled", True),
    )


def load() -> list[SourceConfig]:
    """Read the registry. If absent, seed it with the PoC sources and persist."""
    if not os.path.exists(REGISTRY_PATH):
        save(POC_SEED)
        return list(POC_SEED)
    with open(REGISTRY_PATH) as f:
        return [_from_dict(d) for d in json.load(f)]


def save(sources: list[SourceConfig]) -> None:
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    with open(REGISTRY_PATH, "w") as f:
        json.dump([_to_dict(c) for c in sources], f, indent=2)


def add_source(cfg: SourceConfig) -> None:
    """What the admin UI's 'Connect a source' button calls."""
    current = load()
    current = [c for c in current if c.source_id != cfg.source_id]  # upsert
    current.append(cfg)
    save(current)
