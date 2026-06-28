"""Live ManageIQ collector (Spec 2) — replaces the miq_snapshot shortcut.

LM-1 retired the local appliance, so the PoC join reads a *synthesized* snapshot
(join/miq_snapshot.py). This module is the REAL collector: it talks to a live
ManageIQ REST API via join.miq_client and writes the SAME two artifacts the
pipeline already consumes —
    out/miq/vms.json              (inventory: id, name, vendor, uid_ems, ems_ref)
    out/miq/metric_rollups.json   (utilization: per-VM hourly cpu/mem %)
— so db.loader and the join are unchanged. The only difference from the snapshot
is WHERE the data comes from (a live appliance vs. synthesized), exactly like
the connector upload-vs-S3 story.

It cannot be exercised against a live VMDB here (no appliance, no creds), so it
is built behind an injectable `client` so it is fully verified against a fake in
tests, and degrades honestly: a per-VM metrics fetch that fails is logged and
skipped (one bad VM must not sink the whole collection), mirroring the
dispatcher's fail-soft contract.

VMDB field mapping (GOTCHA J-3):
  - rollup `cpu_usage_rate_average`     -> cpu_usage_pct
  - rollup `mem_usage_absolute_average` -> mem_usage_pct
  - rollup `timestamp`                  -> timestamp (UTC ISO; loader re-normalizes, H-8)
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from join import miq_client


def _vendor_fields(vm: dict) -> dict:
    """Project a raw /api/vms resource onto the inventory shape the join needs.
    ManageIQ returns many attributes; we keep only the join-relevant ones (the
    same five miq_snapshot synthesizes), so the two paths are interchangeable."""
    return {
        "id": vm.get("id"),
        "name": vm.get("name", ""),
        "vendor": (vm.get("vendor") or "").lower(),
        "uid_ems": vm.get("uid_ems"),
        "ems_ref": vm.get("ems_ref"),
    }


def collect_vms(client=miq_client) -> list[dict]:
    """Fetch + project the VM inventory from the live appliance."""
    raw = client.get_vms()
    return [_vendor_fields(v) for v in raw]


def collect_utilization(vms: list[dict], client=miq_client) -> list[dict]:
    """Fetch hourly metric_rollups for each VM and map to the miq_utilization
    shape. Fail-soft per VM: a metrics fetch that errors is logged and skipped
    so one bad VM doesn't sink the whole collection (dispatcher-style)."""
    rows: list[dict] = []
    for vm in vms:
        vm_id = vm.get("id")
        if vm_id is None:
            continue
        try:
            rollups = client.get_metric_rollups(vm_id)
        except Exception as e:  # MIQHTTPError, network, etc. — never fatal
            print(f"[miq-collect] vm {vm_id}: metric_rollups failed: {e} — skipped")
            continue
        for r in rollups:
            cpu = r.get("cpu_usage_rate_average")
            mem = r.get("mem_usage_absolute_average")
            ts = r.get("timestamp")
            if ts is None or (cpu is None and mem is None):
                continue  # a rollup with no usable signal — skip, don't invent
            interval_name = (r.get("capture_interval_name") or "hourly").lower()
            interval_secs = {"hourly": 3600, "daily": 86400}.get(interval_name, 3600)
            rows.append({
                "miq_vm_id": vm_id,
                "timestamp": ts,
                "capture_interval": interval_secs,
                "cpu_usage_pct": round(float(cpu), 4) if cpu is not None else None,
                "mem_usage_pct": round(float(mem), 4) if mem is not None else None,
                "resource_name": vm.get("name", ""),
            })
    return rows


def write_snapshots(client=miq_client) -> tuple[str, str]:
    """Collect from the live appliance and write the same two files the
    synthesized snapshot writes, so the loader/join are unchanged. Drop-in
    replacement for join.miq_snapshot.write_snapshots()."""
    vms = collect_vms(client)
    util = collect_utilization(vms, client)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "out", "miq")
    os.makedirs(out_dir, exist_ok=True)
    vms_path = os.path.join(out_dir, "vms.json")
    util_path = os.path.join(out_dir, "metric_rollups.json")
    with open(vms_path, "w") as f:
        json.dump(vms, f, indent=2)
    with open(util_path, "w") as f:
        json.dump(util, f, indent=2)
    print(f"[miq-collect] wrote {len(vms)} vms -> {vms_path}")
    print(f"[miq-collect] wrote {len(util)} rollup rows -> {util_path}")
    return vms_path, util_path


if __name__ == "__main__":
    # Live run: requires MIQ_URL / MIQ_USER / MIQ_PASS / MIQ_CA_BUNDLE env.
    write_snapshots()
