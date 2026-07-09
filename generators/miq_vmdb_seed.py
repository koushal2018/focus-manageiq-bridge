"""Generate SQL to seed the appliance's VMDB with synthetic inventory.

This bypasses ManageIQ's provider-refresh path entirely (per GOTCHA G-8 we
discovered that synthetic providers can't auth, and per J-4 the VMDB schema
is permissive enough that direct SQL works). The seed file is idempotent:
it uses fixed IDs in a 'demo' range, deletes prior demo rows, then inserts.

What it writes:
  - vms rows with uid_ems (cloud-side instance ID) and ems_ref (Azure ARM
    path for the Azure workloads) -- gotcha J-1's asymmetric join keys.
  - hardwares rows linked to each vm (cpu_total_cores, memory_mb).
  - metric_rollups rows for each cloud VM (24 hourly samples of
    cpu_usage_rate_average and mem_usage_absolute_average).
  - 'On-prem only' VMs with NO cloud ResourceId equivalent --- SPEC s3.1's
    join landmine.

Names DIFFER from the cloud-side names per Workload.name_in_provider()
so the join must reconcile name drift.

Output: out/generators/miq_vmdb_seed.sql

Security note (Bandit B608): this module string-builds SQL BY DESIGN — it
emits a .sql script file for psql; there is no DB connection here, so
driver-level parameterization does not apply. Every value comes from the
in-repo synthetic WORKLOADS constants (no user input) and all strings pass
through _q() (quote-doubling). The `nosec` markers below record that this
was reviewed, not overlooked.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators import common

# Fixed ID ranges so the seed is idempotent.
DEMO_VM_ID_START = 90_001
DEMO_HARDWARE_ID_START = 90_001
DEMO_METRIC_ID_START = 900_001

# These are the real ext_management_systems IDs on our appliance, captured
# during probing. If the appliance is re-provisioned these may shift --- the
# SQL probes for the matching name as a guard.
EMS_NAME_TO_TYPE = {
    "anybank-aws": "ManageIQ::Providers::Amazon::CloudManager",
    "anybank-azure-synthetic": "ManageIQ::Providers::Azure::CloudManager",
    "anybank-oci-synthetic": "ManageIQ::Providers::OracleCloud::CloudManager",
}


def _q(s: str | None) -> str:
    """SQL-quote a string, treating None as NULL."""
    if s is None:
        return "NULL"
    return "'" + s.replace("'", "''") + "'"


def _ems_lookup_subquery(name: str) -> str:
    """Resolve ems_id by name at insert-time --- works even if IDs shift."""
    return (
        f"(SELECT id FROM ext_management_systems "  # nosec B608 — emitted SQL, _q()-quoted constants
        f"WHERE name = {_q(name)} LIMIT 1)"
    )


def build_sql() -> str:
    lines: list[str] = []
    add = lines.append

    add("-- ============================================================")
    add("-- anybank-multicloud-finops-poc :: ManageIQ VMDB synthetic seed")
    add("-- ============================================================")
    add("-- This script seeds vms / hardwares / metric_rollups DIRECTLY,")
    add("-- bypassing the provider-refresh path (see GOTCHAS.md G-8/J-4).")
    add("-- Idempotent: rerun cleans demo rows in the 90000+ id range.")
    add("--")
    add("-- The data deliberately conflicts with the cloud-side cost-export")
    add("-- generators (aws_cur.py / azure_cost_export.py / oci_usage.py)")
    add("-- per SPEC s3.1 to exercise the FOCUS<->MIQ join.")
    add("-- ============================================================")
    add("")
    # `ON_ERROR_STOP` so the first real error surfaces instead of being
    # buried under cascading 'transaction aborted' messages (GOTCHA J-5).
    add("\\set ON_ERROR_STOP on")
    add("BEGIN;")
    add("")
    add("-- Clean previous demo rows (idempotency)")
    add("DELETE FROM metric_rollups WHERE id >= 900000;")
    add("DELETE FROM hardwares      WHERE id >= 90000;")
    add("DELETE FROM vms            WHERE id >= 90000;")
    add("")

    vm_id = DEMO_VM_ID_START
    hw_id = DEMO_HARDWARE_ID_START
    metric_id = DEMO_METRIC_ID_START
    now = dt.datetime(2026, 6, 4, 12, 0, 0)  # fixed reference time

    for wl in common.WORKLOADS:
        miq_name = wl.name_in_provider("miq")  # canonical
        is_onprem = wl.is_on_prem_only()

        # Which provider does MIQ attribute this VM to?
        if wl.aws_instance_id and not wl.azure_resource_id:
            ems_name = "anybank-aws"
            vendor = "amazon"
            uid_ems = wl.aws_instance_id
            ems_ref = wl.aws_instance_id  # AWS: ems_ref == uid_ems
            location = "me-central-1a"
            is_cloud = True
        elif wl.azure_resource_id and not wl.aws_instance_id and not wl.oci_resource_id:
            ems_name = "anybank-azure-synthetic"
            vendor = "azure"
            # On Azure: uid_ems is the GUID at the end of the ARM path;
            # ems_ref is the FULL ARM path. The Azure cost export uses the
            # FULL ARM path as its ResourceId --- GOTCHA J-1's asymmetry.
            uid_ems = (wl.azure_resource_id or "").split("/")[-1]
            ems_ref = wl.azure_resource_id
            location = "uaenorth"
            is_cloud = True
        elif wl.aws_instance_id and wl.azure_resource_id:
            # Cross-cloud workload (the KYC case). MIQ has two rows --- one
            # per provider --- because each provider sees its own instance.
            # We emit the AWS row here; the Azure row gets emitted at the
            # next branch below. We achieve this by NOT setting ems_name
            # uniquely; instead we'll emit a second loop pass.
            ems_name = "anybank-aws"
            vendor = "amazon"
            uid_ems = wl.aws_instance_id
            ems_ref = wl.aws_instance_id
            location = "me-central-1a"
            is_cloud = True
        elif wl.oci_resource_id:
            ems_name = "anybank-oci-synthetic"
            vendor = "oracle"
            uid_ems = wl.oci_resource_id
            ems_ref = wl.oci_resource_id
            location = "me-dubai-1-ad-1"
            is_cloud = True
        else:
            # On-prem only --- no ems_id attribution (or use a special
            # 'on-prem' provider if we had one). Leave ems_id NULL so the
            # join logic must handle the 'orphan' case.
            ems_name = None
            vendor = "redhat"
            uid_ems = None
            ems_ref = None
            location = "AnyBank-DC1"
            is_cloud = False

        ems_lookup = _ems_lookup_subquery(ems_name) if ems_name else "NULL"

        add(f"-- Workload: {wl.canonical_name} ({'on-prem' if is_onprem else ems_name})")
        add(
            "INSERT INTO vms ("  # nosec B608 — emitted SQL, _q()-quoted constants
            "id, name, vendor, location, ems_id, ems_ref, uid_ems, "
            "power_state, raw_power_state, cloud, template, retired, "
            "created_on, updated_on, type"
            ") VALUES ("
            f"{vm_id}, {_q(miq_name)}, {_q(vendor)}, {_q(location)}, "
            f"{ems_lookup}, {_q(ems_ref)}, {_q(uid_ems)}, "
            f"'on', 'on', {str(is_cloud).lower()}, false, false, "
            f"'{now.isoformat()}', '{now.isoformat()}', "
            # type column drives Rails STI on the model side; for cloud VMs
            # it's e.g. ManageIQ::Providers::Amazon::CloudManager::Vm
            f"{_q('ManageIQ::Providers::' + ({'amazon':'Amazon','azure':'Azure','oracle':'OracleCloud'}.get(vendor, 'Infra')) + '::CloudManager::Vm' if is_cloud else 'ManageIQ::Providers::Vmware::InfraManager::Vm')}"
            ");"
        )

        # hardwares row
        add(
            "INSERT INTO hardwares ("  # nosec B608 — emitted SQL, _q()-quoted constants
            "id, vm_or_template_id, cpu_total_cores, cpu_cores_per_socket, "
            "cpu_sockets, memory_mb"
            ") VALUES ("
            f"{hw_id}, {vm_id}, {wl.cpu_cores}, {wl.cpu_cores}, 1, {wl.memory_mb}"
            ");"
        )

        # 24 hourly metric_rollups for the prior day (so the appliance has
        # last-24h utilization data). Use the workload's per-VM averages
        # with small per-hour jitter so the points aren't identical.
        for h in range(24):
            ts = now - dt.timedelta(hours=24 - h)
            # +/- 5% jitter, deterministic from id
            jitter = ((vm_id * 13 + h * 7) % 11 - 5) / 100.0
            cpu = max(0.0, min(100.0, wl.cpu_pct * (1.0 + jitter)))
            mem = max(0.0, min(100.0, wl.mem_pct * (1.0 + jitter)))
            add(
                "INSERT INTO metric_rollups ("  # nosec B608 — emitted SQL, _q()-quoted constants
                "id, resource_type, resource_id, timestamp, "
                "capture_interval, capture_interval_name, "
                "cpu_usage_rate_average, mem_usage_absolute_average, "
                "resource_name, created_on"
                ") VALUES ("
                f"{metric_id}, 'VmOrTemplate', {vm_id}, '{ts.isoformat()}', "
                f"3600, 'hourly', "
                f"{cpu:.4f}, {mem:.4f}, "
                f"{_q(miq_name)}, '{now.isoformat()}'"
                ");"
            )
            metric_id += 1

        vm_id += 1
        hw_id += 1
        add("")

        # Cross-cloud second insert: a workload with BOTH aws+azure IDs gets
        # a second VM row attributed to Azure as well.
        if wl.aws_instance_id and wl.azure_resource_id:
            arm = wl.azure_resource_id
            uid_ems_az = arm.split("/")[-1]
            add(f"-- ...same workload as above, second MIQ inventory row on Azure")
            add(
                "INSERT INTO vms ("  # nosec B608 — emitted SQL, _q()-quoted constants
                "id, name, vendor, location, ems_id, ems_ref, uid_ems, "
                "power_state, raw_power_state, cloud, template, retired, "
                "created_on, updated_on, type"
                ") VALUES ("
                f"{vm_id}, {_q(miq_name)}, 'azure', 'uaenorth', "
                f"{_ems_lookup_subquery('anybank-azure-synthetic')}, "
                f"{_q(arm)}, {_q(uid_ems_az)}, "
                f"'on', 'on', true, false, false, "
                f"'{now.isoformat()}', '{now.isoformat()}', "
                f"'ManageIQ::Providers::Azure::CloudManager::Vm'"
                ");"
            )
            add(
                "INSERT INTO hardwares ("  # nosec B608 — emitted SQL, _q()-quoted constants
                "id, vm_or_template_id, cpu_total_cores, cpu_cores_per_socket, "
                "cpu_sockets, memory_mb"
                ") VALUES ("
                f"{hw_id}, {vm_id}, {wl.cpu_cores}, {wl.cpu_cores}, 1, {wl.memory_mb}"
                ");"
            )
            for h in range(24):
                ts = now - dt.timedelta(hours=24 - h)
                jitter = ((vm_id * 13 + h * 7) % 11 - 5) / 100.0
                cpu = max(0.0, min(100.0, wl.cpu_pct * (1.0 + jitter)))
                mem = max(0.0, min(100.0, wl.mem_pct * (1.0 + jitter)))
                add(
                    "INSERT INTO metric_rollups ("  # nosec B608 — emitted SQL, _q()-quoted constants
                    "id, resource_type, resource_id, timestamp, "
                    "capture_interval, capture_interval_name, "
                    "cpu_usage_rate_average, mem_usage_absolute_average, "
                    "resource_name, created_on"
                    ") VALUES ("
                    f"{metric_id}, 'VmOrTemplate', {vm_id}, '{ts.isoformat()}', "
                    f"3600, 'hourly', "
                    f"{cpu:.4f}, {mem:.4f}, "
                    f"{_q(miq_name)}, '{now.isoformat()}'"
                    ");"
                )
                metric_id += 1
            vm_id += 1
            hw_id += 1
            add("")

    add("COMMIT;")
    add("")
    add("-- Verify with:")
    add("--   SELECT id, name, vendor, uid_ems, ems_ref FROM vms WHERE id >= 90000 ORDER BY id;")
    add("--   SELECT COUNT(*) FROM metric_rollups WHERE id >= 900000;")
    return "\n".join(lines)


def write_sql(path: str | None = None) -> str:
    sql = build_sql()
    out = path or os.path.join(common.out_dir(), "miq_vmdb_seed.sql")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(sql)
    return out


if __name__ == "__main__":
    p = write_sql()
    print(f"wrote {p}")
