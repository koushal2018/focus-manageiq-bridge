"""On-prem cost model for the FOCUS<->ManageIQ join slice.

This module replaces what was originally going to be a thin wrapper over
ManageIQ's `chargeback_rates` + `chargeback_rate_details` tables. After
LM-1 retired the live appliance, we fell back to ENBD's own per-resource
formula (SPEC §0): vCPU rate + memory-GB rate, expressed as a monthly
recharge number (see GOTCHA O-2 --- "burndown" was misleading).

Rates are parameterized via env vars; defaults are placeholders the EBA
team replaces with the chargeback-owner's real rates.

Outputs FOCUS-shaped rows that land in `focus.miq_onprem_cost`. Doing it
this way means the on-prem half of requirement #3 has the SAME columns
as the cloud half --- the view layer can union them without special-casing.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators import common


# --- rate table (USD; EBA team replaces these) ---
RATE_CPU_USD_PER_CORE_MONTH = float(
    os.environ.get("ONPREM_RATE_CPU_USD_PER_CORE_MONTH", "50.0")
)
RATE_MEM_USD_PER_GB_MONTH = float(
    os.environ.get("ONPREM_RATE_MEM_USD_PER_GB_MONTH", "5.0")
)

# Period the recharge applies to. Default is the most recent full calendar
# month relative to a fixed reference date. We use a fixed reference so
# output is reproducible (see SPEC §3.1 + RNG_SEED policy in generators).
REFERENCE_DATE = dt.date(2026, 6, 1)


def _last_full_month(today: dt.date) -> tuple[dt.date, dt.date]:
    """Return (period_start, period_end) for the last full calendar month."""
    first_of_this = dt.date(today.year, today.month, 1)
    period_end = first_of_this - dt.timedelta(days=1)
    period_start = dt.date(period_end.year, period_end.month, 1)
    return period_start, period_end


def compute_rows(reference: dt.date | None = None) -> list[dict]:
    """For each on-prem-only workload in common.WORKLOADS, emit one FOCUS-
    shaped recharge row.
    """
    reference = reference or REFERENCE_DATE
    period_start, period_end = _last_full_month(reference)

    # MIQ-side IDs were assigned during the slice-1 seed. We mirror the same
    # sequence here so the resource_join_map's unmatched_miq_only rows match
    # this output by name.
    vm_id_for: dict[str, int] = {}
    vm_id = 90_001
    for wl in common.WORKLOADS:
        vm_id_for[wl.canonical_name] = vm_id
        vm_id += 1
        if wl.aws_instance_id and wl.azure_resource_id:
            # cross-cloud workload claims two MIQ ids
            vm_id += 1

    rows: list[dict] = []
    for wl in common.WORKLOADS:
        if not wl.is_on_prem_only():
            continue
        gb = wl.memory_mb / 1024.0
        monthly = (
            RATE_CPU_USD_PER_CORE_MONTH * wl.cpu_cores
            + RATE_MEM_USD_PER_GB_MONTH * gb
        )
        rows.append({
            "miq_vm_id": vm_id_for[wl.canonical_name],
            "charge_period_start": period_start.isoformat(),
            "charge_period_end": period_end.isoformat(),
            # No chargeback_rate_id yet --- slice 6 successor wiring would
            # set this when reading from MIQ's chargeback_rate_details.
            "chargeback_rate_id": None,
            "billed_cost": round(monthly, 6),
            "billing_currency": "USD",
            "service_category": "Compute",
            "service_name": "On-Prem VM (recharge)",
            "sub_account_id": wl.business_unit,
            "notes": (
                f"Formula: {RATE_CPU_USD_PER_CORE_MONTH:.2f} USD/core/mo "
                f"× {wl.cpu_cores} cores + {RATE_MEM_USD_PER_GB_MONTH:.2f} "
                f"USD/GB/mo × {gb:.1f} GB. Stub rates --- replace with the "
                "ENBD chargeback table per GOTCHA O-1."
            ),
        })
    return rows


def load_into_postgres(
    container: str = "finops_pg",  # retained for back-compat; ignored in network mode
    user: str = "focus_app",
    db: str = "focus",
) -> int:
    """Truncate + reload focus.miq_onprem_cost from compute_rows().

    Reuses db.loader.psql_argv() so the connection mode (docker exec for
    local dev, network psql for containers/prod) is decided in one place.
    """
    import csv, io
    from db.loader import psql_argv  # single source of connection-mode truth

    rows = compute_rows()
    buf = io.StringIO()
    cols = [
        "miq_vm_id", "charge_period_start", "charge_period_end",
        "chargeback_rate_id", "billed_cost", "billing_currency",
        "service_category", "service_name", "sub_account_id", "notes",
    ]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r[k] is None else r[k]) for k in cols})

    subprocess.run(
        psql_argv(db=db, user=user) + ["-c", "TRUNCATE miq_onprem_cost RESTART IDENTITY"],
        check=True, capture_output=True,
    )
    proc = subprocess.run(
        psql_argv(db=db, user=user) + [
            "-c",
            f"\\COPY miq_onprem_cost ({', '.join(cols)}) "
            f"FROM STDIN WITH (FORMAT csv, HEADER true, "
            f"FORCE_NULL (chargeback_rate_id))"],
        input=buf.getvalue().encode(), capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode())
    out = (proc.stdout or b"").decode()
    for line in out.splitlines():
        if line.strip().startswith("COPY "):
            return int(line.strip().split()[1])
    return 0


if __name__ == "__main__":
    rows = compute_rows()
    print(f"computed {len(rows)} on-prem recharge rows for "
          f"{rows[0]['charge_period_start']} → {rows[0]['charge_period_end']}"
          if rows else "no on-prem rows")
    for r in rows:
        print(f"  miq_vm_id={r['miq_vm_id']:>5} {r['service_name']:<30} "
              f"${r['billed_cost']:>10.2f} {r['billing_currency']}  ({r['sub_account_id']})")
    if "--load" in sys.argv:
        n = load_into_postgres()
        print(f"loaded {n} rows into focus.miq_onprem_cost")
