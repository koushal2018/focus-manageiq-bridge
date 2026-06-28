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
# Seeded with NATIVE FOCUS exports (post-NF-1) — what the providers' consoles
# actually produce today. The CUR/cost-export source types still exist (for
# historical data) but the default demo uses the native-FOCUS path.
POC_SEED: list[SourceConfig] = [
    SourceConfig(
        source_id="aws-payer-demo",
        source_type="aws-focus-export",
        display_name="AWS — FOCUS 1.2 export (DEMO payer 999900001111)",
        location="out/generators/focus_aws.csv",
        credential_ref="demo:no-credential-needed-synthetic",
        schedule="daily",
    ),
    SourceConfig(
        source_id="azure-sub-demo",
        source_type="azure-focus-export",
        display_name="Azure — FOCUS 1.2 export (DEMO subscription)",
        location="out/generators/focus_azure.csv",
        credential_ref="demo:no-credential-needed-synthetic",
        schedule="daily",
    ),
    SourceConfig(
        source_id="oci-tenancy-demo",
        source_type="oci-focus-export",
        display_name="OCI — FOCUS 1.0 export (DEMO tenancy)",
        location="out/generators/focus_oci.csv",
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


import contextlib
import fcntl
import tempfile


@contextlib.contextmanager
def _registry_lock():
    """Advisory exclusive lock for read-modify-write of the registry.

    The registry is a shared JSON file; `add_source`/`remove` are
    read-modify-write, so two concurrent requests would lose an update
    (classic race). An flock on a sidecar `.lock` serializes them. POSIX
    advisory lock — fine for the single-host container; a multi-replica
    deployment would move the registry to a DB row with a transaction
    (Spec 4). Lock is held only for the brief mutate, never across I/O."""
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    lock_path = REGISTRY_PATH + ".lock"
    f = open(lock_path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def load() -> list[SourceConfig]:
    """Read the registry. If absent, seed it with the PoC sources and persist."""
    if not os.path.exists(REGISTRY_PATH):
        save(POC_SEED)
        return list(POC_SEED)
    with open(REGISTRY_PATH) as f:
        return [_from_dict(d) for d in json.load(f)]


def save(sources: list[SourceConfig]) -> None:
    """Atomic write: serialize to a temp file in the same dir, then os.replace
    (atomic rename on POSIX) so a crash mid-write can never leave a truncated /
    corrupt registry — the old file stays intact until the new one is complete."""
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(REGISTRY_PATH), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump([_to_dict(c) for c in sources], f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, REGISTRY_PATH)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def add_source(cfg: SourceConfig) -> None:
    """What the admin UI's 'Connect a source' button calls. Locked
    read-modify-write so concurrent registrations don't lose each other."""
    with _registry_lock():
        current = load()
        current = [c for c in current if c.source_id != cfg.source_id]  # upsert
        current.append(cfg)
        save(current)


def remove_source(source_id: str) -> None:
    """Locked removal — mirror of add_source so the two can't race."""
    with _registry_lock():
        current = [c for c in load() if c.source_id != source_id]
        save(current)
