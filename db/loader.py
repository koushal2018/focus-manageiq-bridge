"""Load slice 1-3 outputs into the focus database.

Connects to Postgres via `docker exec -i manageiq_appliance psql ...` ---
the appliance's port 5432 is not host-mapped (GOTCHA D-3). For the slice-4
PoC this is acceptable; the env vars FOCUS_PG_* let the loader retarget
a standalone Postgres later (GOTCHA D-1's exit ramp).

Pipeline:
  1) TRUNCATE the target tables (idempotent reload)
  2) COPY focus_costs FROM out/normalizer/focus_combined.csv
  3) COPY resource_join_map FROM out/join/resource_join_map.csv
  4) Pull metric_rollups from appliance vmdb_production for VMs in the
     90000+ demo range and INSERT into miq_utilization
  5) INSERT one load_metadata row recording the run

We deliberately do NOT use psycopg2: stdlib + docker exec keeps the loader
zero-dependency, which matches the EBA team's "fewest moving parts" goal.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import subprocess
import sys

# Module path bootstrap so `python3 -m db.loader` works from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- connection env (GOTCHA D-1 exit ramp) ---
FOCUS_PG_CONTAINER = os.environ.get("FOCUS_PG_CONTAINER", "manageiq_appliance")
FOCUS_PG_USER = os.environ.get("FOCUS_PG_USER", "focus_app")
FOCUS_PG_DB = os.environ.get("FOCUS_PG_DB", "focus")
# vmdb_production lives on the same Postgres server (the appliance), accessed
# as the postgres superuser so we can read metric_rollups regardless of grants.
VMDB_USER = os.environ.get("VMDB_PG_USER", "postgres")
VMDB_DB = os.environ.get("VMDB_PG_DB", "vmdb_production")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOCUS_CSV = os.path.join(ROOT, "out", "normalizer", "focus_combined.csv")
JOIN_CSV = os.path.join(ROOT, "out", "join", "resource_join_map.csv")


def run_psql(sql: str, db: str = FOCUS_PG_DB, user: str = FOCUS_PG_USER) -> str:
    """Run a single SQL statement via docker exec psql. Returns stdout."""
    cmd = [
        "docker", "exec", "-i", FOCUS_PG_CONTAINER,
        "psql", "-U", user, "-d", db, "-v", "ON_ERROR_STOP=1",
        "-c", sql,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"psql failed (rc={proc.returncode})\n--- stderr ---\n{proc.stderr}"
        )
    return proc.stdout


def copy_csv_to_table(csv_path: str, table: str, columns: list[str]) -> int:
    """Stream a CSV file into a Postgres table via psql \\COPY.

    Strips columns we don't want to load (e.g. the focus CSV's blank ones
    for FOCUS columns the loader doesn't carry). We pass the column list
    explicitly to handle column ordering and missing-columns gracefully.
    """
    # We use \COPY (client-side) so file paths resolve on the host, then
    # pipe stdin through docker exec.
    col_list = ", ".join(columns)
    sql = (
        f"\\COPY {table} ({col_list}) "
        f"FROM STDIN WITH (FORMAT csv, HEADER true, FORCE_NULL ({col_list}))"
    )
    cmd = [
        "docker", "exec", "-i", FOCUS_PG_CONTAINER,
        "psql", "-U", FOCUS_PG_USER, "-d", FOCUS_PG_DB, "-v", "ON_ERROR_STOP=1",
        "-c", sql,
    ]
    with open(csv_path, "rb") as f:
        proc = subprocess.run(cmd, stdin=f, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"\\COPY into {table} failed (rc={proc.returncode})\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    # psql prints "COPY <n>" to stdout
    out = (proc.stdout or "").strip()
    for line in out.splitlines():
        if line.startswith("COPY "):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                pass
    return 0


# --- focus_costs loader ---

# The focus_combined.csv has the FOCUS display names. Map them to our
# snake_case schema columns, in the order the CSV provides them.
FOCUS_CSV_TO_DB_COLUMN = {
    "_source": "source",
    "BillingAccountId": "billing_account_id",
    "BillingAccountName": "billing_account_name",
    "BillingPeriodStart": "billing_period_start",
    "BillingPeriodEnd": "billing_period_end",
    "ChargePeriodStart": "charge_period_start",
    "ChargePeriodEnd": "charge_period_end",
    "SubAccountId": "sub_account_id",
    "SubAccountName": "sub_account_name",
    "ChargeCategory": "charge_category",
    "ChargeDescription": "charge_description",
    "ChargeFrequency": "charge_frequency",
    "BilledCost": "billed_cost",
    "EffectiveCost": "effective_cost",
    "ListCost": "list_cost",
    "ContractedCost": "contracted_cost",
    "BillingCurrency": "billing_currency",
    "PricingCurrency": "pricing_currency",
    "ServiceProviderName": "service_provider_name",
    "InvoiceIssuerName": "invoice_issuer_name",
    "ServiceCategory": "service_category",
    "ServiceSubcategory": "service_subcategory",
    "ServiceName": "service_name",
    "SkuId": "sku_id",
    "SkuMeter": "sku_meter",
    "SkuPriceId": "sku_price_id",
    "ResourceId": "resource_id",
    "ResourceName": "resource_name",
    "ResourceType": "resource_type",
    "RegionId": "region_id",
    "RegionName": "region_name",
    "AvailabilityZone": "availability_zone",
    "ConsumedQuantity": "consumed_quantity",
    "ConsumedUnit": "consumed_unit",
    "PricingQuantity": "pricing_quantity",
    "PricingUnit": "pricing_unit",
    "Tags": "tags",
}


def load_focus_costs() -> int:
    """Rewrite the FOCUS CSV with DB-friendly column names + NULL handling, then COPY."""
    # We need to transform the CSV: rename columns to snake_case, convert
    # empty strings to NULL (Postgres COPY honors NULL '\N' or FORCE_NULL).
    # Easier path: rewrite to a staging CSV.
    staged = os.path.join(ROOT, "out", "db_staging_focus_costs.csv")
    with open(FOCUS_CSV) as f, open(staged, "w", newline="") as g:
        reader = csv.DictReader(f)
        db_cols = [FOCUS_CSV_TO_DB_COLUMN[c] for c in reader.fieldnames if c in FOCUS_CSV_TO_DB_COLUMN]
        writer = csv.DictWriter(g, fieldnames=db_cols)
        writer.writeheader()
        for row in reader:
            out: dict[str, str] = {}
            for csv_col, db_col in FOCUS_CSV_TO_DB_COLUMN.items():
                if csv_col not in row:
                    continue
                v = row[csv_col]
                # Empty Tags -> NULL JSON
                if db_col == "tags" and not v:
                    v = ""
                out[db_col] = v
            writer.writerow(out)

    # Push staged file into the container, then \COPY from there. We need
    # the file inside the container because Postgres COPY runs server-side
    # and docker exec stdin would require client-side \COPY anyway. Use
    # docker cp.
    # Client-side \COPY (no superuser needed). See GOTCHA D-4.
    col_list = ", ".join(FOCUS_CSV_TO_DB_COLUMN.values())
    sql = (
        f"\\COPY focus_costs ({col_list}) "
        f"FROM STDIN WITH (FORMAT csv, HEADER true, FORCE_NULL ({col_list}))"
    )
    cmd = [
        "docker", "exec", "-i", FOCUS_PG_CONTAINER,
        "psql", "-U", FOCUS_PG_USER, "-d", FOCUS_PG_DB, "-v", "ON_ERROR_STOP=1",
        "-c", sql,
    ]
    with open(staged, "rb") as f:
        proc = subprocess.run(cmd, stdin=f, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"focus_costs \\COPY failed: {proc.stderr.decode()}")
    out = (proc.stdout or b"").decode()
    for line in out.splitlines():
        if line.strip().startswith("COPY "):
            return int(line.strip().split()[1])
    return 0


# --- resource_join_map loader ---

JOIN_CSV_TO_DB_COLUMN = {
    "status": "status",
    "focus_source": "focus_source",
    "focus_resource_id": "focus_resource_id",
    "focus_service_category": "focus_service_category",
    "focus_billed_cost_sum": "focus_billed_cost_sum",
    "focus_row_count": "focus_row_count",
    "miq_vm_id": "miq_vm_id",
    "miq_vm_name": "miq_vm_name",
    "miq_vendor": "miq_vendor",
    "miq_uid_ems": "miq_uid_ems",
    "miq_ems_ref": "miq_ems_ref",
    "join_key_used": "join_key_used",
    "notes": "notes",
}


def load_join_map() -> int:
    staged = os.path.join(ROOT, "out", "db_staging_join_map.csv")
    with open(JOIN_CSV) as f, open(staged, "w", newline="") as g:
        reader = csv.DictReader(f)
        db_cols = list(JOIN_CSV_TO_DB_COLUMN.values())
        writer = csv.DictWriter(g, fieldnames=db_cols)
        writer.writeheader()
        for row in reader:
            writer.writerow({JOIN_CSV_TO_DB_COLUMN[k]: row.get(k, "") for k in JOIN_CSV_TO_DB_COLUMN})

    col_list = ", ".join(JOIN_CSV_TO_DB_COLUMN.values())
    sql = (
        f"\\COPY resource_join_map ({col_list}) "
        f"FROM STDIN WITH (FORMAT csv, HEADER true, FORCE_NULL ({col_list}))"
    )
    cmd = [
        "docker", "exec", "-i", FOCUS_PG_CONTAINER,
        "psql", "-U", FOCUS_PG_USER, "-d", FOCUS_PG_DB, "-v", "ON_ERROR_STOP=1",
        "-c", sql,
    ]
    with open(staged, "rb") as f:
        proc = subprocess.run(cmd, stdin=f, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"join_map \\COPY failed: {proc.stderr.decode()}")
    out = (proc.stdout or b"").decode()
    for line in out.splitlines():
        if line.strip().startswith("COPY "):
            return int(line.strip().split()[1])
    return 0


# --- miq_utilization loader (cross-DB) ---

def load_miq_utilization() -> int:
    """Pull rollups from vmdb_production for our seeded VMs (id >= 90000)
    and INSERT into focus.miq_utilization.

    GOTCHA J-3: pin resource_type='VmOrTemplate'.
    GOTCHA D-1: vmdb is on the same server, so we can use postgres_fdw or
    just two psql calls. We go with two psql calls --- no extension needed.
    """
    # Read rollups as CSV from vmdb_production
    select_sql = (
        "SELECT resource_id AS miq_vm_id, timestamp, capture_interval, "
        "cpu_usage_rate_average, mem_usage_absolute_average, resource_name "
        "FROM metric_rollups "
        "WHERE resource_type = 'VmOrTemplate' AND resource_id >= 90000 "
        "ORDER BY resource_id, timestamp"
    )
    # \copy ... to stdout streams rows; pipe into the focus DB \copy
    export_cmd = [
        "docker", "exec", "-i", FOCUS_PG_CONTAINER,
        "psql", "-U", VMDB_USER, "-d", VMDB_DB, "-v", "ON_ERROR_STOP=1",
        "-c", f"\\COPY ({select_sql}) TO STDOUT WITH (FORMAT csv, HEADER true)",
    ]
    export = subprocess.run(export_cmd, capture_output=True, text=True)
    if export.returncode != 0:
        raise RuntimeError(f"vmdb export failed: {export.stderr}")

    csv_bytes = export.stdout.encode()

    import_cmd = [
        "docker", "exec", "-i", FOCUS_PG_CONTAINER,
        "psql", "-U", FOCUS_PG_USER, "-d", FOCUS_PG_DB, "-v", "ON_ERROR_STOP=1",
        "-c",
        "\\COPY miq_utilization "
        "(miq_vm_id, timestamp, capture_interval, cpu_usage_pct, mem_usage_pct, resource_name) "
        "FROM STDIN WITH (FORMAT csv, HEADER true)",
    ]
    proc = subprocess.run(import_cmd, input=csv_bytes, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"miq_utilization import failed: {proc.stderr.decode()}")

    out = (proc.stdout or b"").decode()
    for line in out.splitlines():
        if line.strip().startswith("COPY "):
            return int(line.strip().split()[1])
    return 0


# --- main ---

def main() -> None:
    started = dt.datetime.now(dt.timezone.utc)
    print(f"[loader] start at {started.isoformat()}")

    # 1. truncate
    print("[loader] truncating target tables...")
    run_psql(
        "TRUNCATE focus_costs, resource_join_map, miq_utilization, miq_onprem_cost "
        "RESTART IDENTITY"
    )

    # 2. focus_costs
    print("[loader] loading focus_costs from", FOCUS_CSV)
    n_focus = load_focus_costs()
    print(f"[loader] focus_costs: {n_focus} rows")

    # 3. resource_join_map
    print("[loader] loading resource_join_map from", JOIN_CSV)
    n_join = load_join_map()
    print(f"[loader] resource_join_map: {n_join} rows")

    # 4. miq_utilization
    print("[loader] loading miq_utilization from appliance vmdb_production...")
    n_util = load_miq_utilization()
    print(f"[loader] miq_utilization: {n_util} rows")

    # 5. metadata
    finished = dt.datetime.now(dt.timezone.utc)
    run_psql(
        "INSERT INTO load_metadata "
        "(started_at, finished_at, focus_rows_loaded, join_rows_loaded, "
        " miq_util_rows_loaded, miq_onprem_rows_loaded, notes) "
        f"VALUES ('{started.isoformat()}', '{finished.isoformat()}', "
        f"{n_focus}, {n_join}, {n_util}, 0, "
        "'slice 4 PoC load; miq_onprem deferred to slice 6')"
    )

    print(f"[loader] done in {(finished - started).total_seconds():.2f}s")


if __name__ == "__main__":
    main()
