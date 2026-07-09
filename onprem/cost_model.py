"""On-prem cost model for the FOCUS<->ManageIQ join slice.

This module replaces what was originally going to be a thin wrapper over
ManageIQ's `chargeback_rates` + `chargeback_rate_details` tables. After
LM-1 retired the live appliance, we fell back to AnyBank's own per-resource
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

    # VM-id assignment comes from the canonical map (GOTCHA H-2) so on-prem
    # rows attach to the same VM ids the join map + snapshot use.
    vm_id_for = common.workload_vm_ids()

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
            "miq_vm_id": vm_id_for[wl.canonical_name][0],
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
                "AnyBank chargeback table per GOTCHA O-1."
            ),
        })
    return rows


def load_into_postgres(
    container: str = "finops_pg",  # retained for signature back-compat; unused
    user: str = "focus_app",
    db: str = "focus",
) -> int:
    """Truncate + reload miq_onprem_cost via psycopg2 in one transaction.

    Uses db.loader's connection settings + COPY helper so the on-prem load
    shares the same psycopg2 path as the main loader (GOTCHA H-4/H-5) — no
    psql shell-out.
    """
    import csv, io
    import psycopg2
    from db.loader import _conn_kwargs, _copy

    rows = compute_rows()
    cols = [
        "miq_vm_id", "charge_period_start", "charge_period_end",
        "chargeback_rate_id", "billed_cost", "billing_currency",
        "service_category", "service_name", "sub_account_id", "notes",
    ]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r[k] is None else r[k]) for k in cols})
    buf.seek(0)

    conn = psycopg2.connect(**_conn_kwargs())
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("TRUNCATE miq_onprem_cost RESTART IDENTITY")
            # only chargeback_rate_id is nullable-empty here
            cur.copy_expert(
                f"COPY miq_onprem_cost ({', '.join(cols)}) FROM STDIN "
                f"WITH (FORMAT csv, HEADER true, FORCE_NULL (chargeback_rate_id))",
                buf,
            )
            n = cur.rowcount
        conn.commit()
        return n
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
