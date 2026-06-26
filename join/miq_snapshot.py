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

# Demo VM id range used by miq_vmdb_seed.py; keep in lockstep with that file.
DEMO_VM_ID_START = 90_001


def synthesize_vms() -> list[dict]:
    """Reconstruct the /api/vms?expand=resources response from common.WORKLOADS.

    Mirrors the inventory the seeded appliance held (vm.id from 90001 onward,
    with cross-cloud workloads emitting two rows --- one per provider).
    """
    vms: list[dict] = []
    vm_id = DEMO_VM_ID_START

    for wl in common.WORKLOADS:
        miq_name = wl.name_in_provider("miq")
        is_onprem = wl.is_on_prem_only()

        if wl.aws_instance_id and not wl.azure_resource_id:
            vendor = "amazon"
            uid_ems = wl.aws_instance_id
            ems_ref = wl.aws_instance_id
        elif wl.azure_resource_id and not wl.aws_instance_id and not wl.oci_resource_id:
            vendor = "azure"
            uid_ems = (wl.azure_resource_id or "").split("/")[-1]
            ems_ref = wl.azure_resource_id
        elif wl.aws_instance_id and wl.azure_resource_id:
            # cross-cloud: emit AWS row here, Azure row below
            vendor = "amazon"
            uid_ems = wl.aws_instance_id
            ems_ref = wl.aws_instance_id
        elif wl.oci_resource_id:
            vendor = "oracle"
            uid_ems = wl.oci_resource_id
            ems_ref = wl.oci_resource_id
        else:
            vendor = "redhat"
            uid_ems = None
            ems_ref = None

        vms.append({
            "id": vm_id,
            "name": miq_name,
            "vendor": vendor,
            "uid_ems": uid_ems,
            "ems_ref": ems_ref,
        })
        vm_id += 1

        if wl.aws_instance_id and wl.azure_resource_id:
            # second row for the Azure side of the cross-cloud workload
            arm = wl.azure_resource_id
            vms.append({
                "id": vm_id,
                "name": miq_name,
                "vendor": "azure",
                "uid_ems": arm.split("/")[-1],
                "ems_ref": arm,
            })
            vm_id += 1

    return vms


def synthesize_utilization() -> list[dict]:
    """Reconstruct the appliance's metric_rollups for the seeded VMs.

    Each VM gets 24 hourly samples mirroring miq_vmdb_seed's jitter formula.
    """
    import datetime as dt
    rows: list[dict] = []
    vm_id = DEMO_VM_ID_START
    now = dt.datetime(2026, 6, 4, 12, 0, 0, tzinfo=dt.timezone.utc)

    for wl in common.WORKLOADS:
        miq_name = wl.name_in_provider("miq")
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
        vm_id += 1
        # cross-cloud second row gets its own 24 samples too
        if wl.aws_instance_id and wl.azure_resource_id:
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
            vm_id += 1

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
