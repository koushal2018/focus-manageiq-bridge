"""Synthesize a MIQ inventory snapshot from the workloads in generators/common.

This module exists because LM-1 forced us to retire the live ManageIQ
appliance. The real /api/vms response shape was captured during slice 3
when we ran the join against the live appliance; that shape is now
reconstructed here from common.WORKLOADS so the join slice still works
without the appliance running.

Why not just commit the captured JSON? Because then the EBA team has TWO
sources of truth for the workloads (common.py and a static JSON file),
and a future edit to common.py would silently desync from the snapshot.
Synthesizing on demand keeps a single source.

Output shape mirrors what /api/vms?expand=resources returned --- the same
fields the live join consumed (id, name, vendor, uid_ems, ems_ref).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators import common

# VM-id assignment is owned by common.workload_vm_ids() (GOTCHA H-2).
DEMO_VM_ID_START = common.DEMO_VM_ID_START


def synthesize_vms() -> list[dict]:
    """Reconstruct the /api/vms?expand=resources response from common.WORKLOADS.

    VM ids come from the canonical common.workload_vm_ids() map so this stays
    in lockstep with the on-prem model and the web queries (H-2).
    """
    vms: list[dict] = []
    id_map = common.workload_vm_ids()

    for wl in common.WORKLOADS:
        miq_name = wl.name_in_provider("miq")
        ids = id_map[wl.canonical_name]

        if wl.aws_instance_id and not wl.azure_resource_id:
            vendor, uid_ems, ems_ref = "amazon", wl.aws_instance_id, wl.aws_instance_id
        elif wl.azure_resource_id and not wl.aws_instance_id and not wl.oci_resource_id:
            vendor = "azure"
            uid_ems = (wl.azure_resource_id or "").split("/")[-1]
            ems_ref = wl.azure_resource_id
        elif wl.aws_instance_id and wl.azure_resource_id:
            vendor, uid_ems, ems_ref = "amazon", wl.aws_instance_id, wl.aws_instance_id
        elif wl.oci_resource_id:
            vendor, uid_ems, ems_ref = "oracle", wl.oci_resource_id, wl.oci_resource_id
        else:
            vendor, uid_ems, ems_ref = "redhat", None, None

        vms.append({
            "id": ids[0], "name": miq_name, "vendor": vendor,
            "uid_ems": uid_ems, "ems_ref": ems_ref,
        })

        if wl.aws_instance_id and wl.azure_resource_id:
            arm = wl.azure_resource_id
            vms.append({
                "id": ids[1], "name": miq_name, "vendor": "azure",
                "uid_ems": arm.split("/")[-1], "ems_ref": arm,
            })

    return vms


def synthesize_utilization() -> list[dict]:
    """Reconstruct the appliance's metric_rollups for the seeded VMs.

    Each VM gets 24 hourly samples mirroring miq_vmdb_seed's jitter formula.
    """
    import datetime as dt
    rows: list[dict] = []
    id_map = common.workload_vm_ids()
    now = dt.datetime(2026, 6, 4, 12, 0, 0, tzinfo=dt.timezone.utc)

    for wl in common.WORKLOADS:
        miq_name = wl.name_in_provider("miq")
        for vm_id in id_map[wl.canonical_name]:   # 1 id, or 2 for cross-cloud
            for h in range(24):
                ts = now - dt.timedelta(hours=24 - h)
                jitter = ((vm_id * 13 + h * 7) % 11 - 5) / 100.0
                cpu = max(0.0, min(100.0, wl.cpu_pct * (1.0 + jitter)))
                mem = max(0.0, min(100.0, wl.mem_pct * (1.0 + jitter)))
                rows.append({
                    "miq_vm_id": vm_id,
                    "timestamp": ts.isoformat(),
                    "capture_interval": 3600,
                    "cpu_usage_pct": round(cpu, 4),
                    "mem_usage_pct": round(mem, 4),
                    "resource_name": miq_name,
                })

    return rows


def write_snapshots() -> tuple[str, str]:
    """Write inventory + utilization JSON snapshots to out/."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "out", "miq")
    os.makedirs(out_dir, exist_ok=True)

    vms_path = os.path.join(out_dir, "vms.json")
    util_path = os.path.join(out_dir, "metric_rollups.json")
    with open(vms_path, "w") as f:
        json.dump(synthesize_vms(), f, indent=2)
    with open(util_path, "w") as f:
        json.dump(synthesize_utilization(), f, indent=2)
    return vms_path, util_path


if __name__ == "__main__":
    a, b = write_snapshots()
    print(f"wrote {a}")
    print(f"wrote {b}")
