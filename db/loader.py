"""Load the pipeline outputs into the FOCUS database — psycopg2, one txn.

Rewritten from the psql-shell-out PoC loader (GOTCHA P-6) to psycopg2 so the
whole reload is:
  - ONE transaction (GOTCHA H-4): truncate + all loads commit together or
    roll back together; the dashboard never serves a torn snapshot.
  - parameterized + COPY via copy_expert (GOTCHA H-5): no SQL built from
    data strings; bulk path scales past the per-statement psql spawn.
  - currency-normalized to USD at load (H-1), money as Decimal-safe text.
  - timestamps normalized to UTC (H-8).
  - x_ provider columns preserved into the `extensions` JSONB (H-9).
  - an idempotency_key per focus row (H-6) for future incremental upserts.

Connection comes from FOCUS_PG_* env (host/port/user/pass/db) — the same
contract web/db.py uses. No docker exec, no psql binary needed.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import os
import sys
from decimal import Decimal, InvalidOperation

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOCUS_CSV = os.path.join(ROOT, "out", "normalizer", "focus_combined.csv")
JOIN_CSV = os.path.join(ROOT, "out", "join", "resource_join_map.csv")
MIQ_UTIL_JSON = os.environ.get(
    "MIQ_UTIL_JSON", os.path.join(ROOT, "out", "miq", "metric_rollups.json")
)


def _conn_kwargs() -> dict:
    return {
        "host":     os.environ.get("FOCUS_PG_HOST", "127.0.0.1"),
        "port": int(os.environ.get("FOCUS_PG_PORT", "5432")),
        "user":     os.environ.get("FOCUS_PG_USER", "focus_app"),
        "password": os.environ.get("FOCUS_PG_PASS", os.environ.get("PGPASSWORD", "focus_app_demo")),
        "dbname":   os.environ.get("FOCUS_PG_DB", "focus"),
        "connect_timeout": 10,
    }


# FOCUS display name -> focus_costs column.
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
    "CommitmentDiscountId": "commitment_discount_id",
    "CommitmentDiscountStatus": "commitment_discount_status",
}

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


def _to_utc_iso(value: str) -> str:
    """Normalize a timestamp string to UTC ISO (GOTCHA H-8).

    Naive timestamps are assumed UTC (documented assumption); aware ones are
    converted. Empty -> empty. Bad -> returned as-is (Postgres will reject,
    surfacing the bad value rather than silently mis-bucketing)."""
    if not value:
        return ""
    try:
        d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).isoformat()


def _idempotency_key(row: dict) -> str:
    """Stable hash of identifying fields (GOTCHA H-6). Lets a future
    incremental load upsert ON CONFLICT instead of duplicating."""
    basis = "|".join(str(row.get(c, "")) for c in (
        "_source", "BillingAccountId", "ResourceId", "SkuMeter",
        "ChargePeriodStart", "ChargeDescription", "BilledCost",
    ))
    return hashlib.sha256(basis.encode()).hexdigest()


def _build_focus_staging() -> tuple[io.StringIO, list[str]]:
    """Transform focus_combined.csv into a COPY-ready buffer with derived
    columns (H-1 currency, H-8 utc, H-9 extensions, H-6 idempotency)."""
    from generators.common import FX_TO_USD

    derived = ["billed_cost_usd", "fx_rate_to_usd", "extensions", "idempotency_key"]
    ts_cols = {"charge_period_start", "charge_period_end"}
    buf = io.StringIO()

    with open(FOCUS_CSV) as f:
        reader = csv.DictReader(f)
        db_cols = [FOCUS_CSV_TO_DB_COLUMN[c] for c in reader.fieldnames if c in FOCUS_CSV_TO_DB_COLUMN]
        all_cols = db_cols + derived
        w = csv.DictWriter(buf, fieldnames=all_cols)
        w.writeheader()
        for row in reader:
            out: dict[str, str] = {}
            for csv_col, db_col in FOCUS_CSV_TO_DB_COLUMN.items():
                if csv_col not in row:
                    continue
                v = row[csv_col]
                if db_col in ts_cols:
                    v = _to_utc_iso(v)        # H-8
                out[db_col] = v

            # Tags must be valid JSON to land in the JSONB column. Real exports
            # carry malformed tag strings (messy-data reality); coerce an
            # unparseable value to NULL rather than aborting the whole COPY.
            tg = out.get("tags")
            if tg:
                try:
                    json.loads(tg)
                except (ValueError, TypeError):
                    out["tags"] = ""   # FORCE_NULL -> SQL NULL

            # H-1 currency normalization → USD
            ccy = (row.get("BillingCurrency") or "").upper()
            rate = FX_TO_USD.get(ccy)
            raw = row.get("BilledCost") or ""
            if rate is not None and raw not in ("", None):
                try:
                    out["billed_cost_usd"] = f"{Decimal(str(raw)) * Decimal(str(rate)):.6f}"
                    out["fx_rate_to_usd"] = f"{rate:.8f}"
                except (InvalidOperation, ValueError):
                    out["billed_cost_usd"] = ""
                    out["fx_rate_to_usd"] = ""
            else:
                out["billed_cost_usd"] = ""
                out["fx_rate_to_usd"] = ""

            # H-9 preserve provider extension columns into JSONB. The native
            # adapter folded x_ columns into _extensions (JSON string); if a
            # source predates that, fall back to scanning x_ columns directly.
            ext_json = row.get("_extensions") or ""
            if not ext_json:
                ext = {k: row[k] for k in row if k.startswith("x_") and row[k] not in ("", None)}
                ext_json = json.dumps(ext) if ext else ""
            out["extensions"] = ext_json

            # H-6 idempotency key
            out["idempotency_key"] = _idempotency_key(row)

            w.writerow(out)

    buf.seek(0)
    return buf, all_cols


def _build_join_staging() -> tuple[io.StringIO, list[str]]:
    buf = io.StringIO()
    with open(JOIN_CSV) as f:
        reader = csv.DictReader(f)
        cols = list(JOIN_CSV_TO_DB_COLUMN.values())
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        for row in reader:
            w.writerow({JOIN_CSV_TO_DB_COLUMN[k]: row.get(k, "") for k in JOIN_CSV_TO_DB_COLUMN})
    buf.seek(0)
    return buf, cols


def _build_util_staging() -> tuple[io.StringIO, list[str]]:
    cols = ["miq_vm_id", "timestamp", "capture_interval",
            "cpu_usage_pct", "mem_usage_pct", "resource_name"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    with open(MIQ_UTIL_JSON) as f:
        for r in json.load(f):
            row = dict(r)
            row["timestamp"] = _to_utc_iso(row.get("timestamp", ""))   # H-8
            w.writerow({k: row.get(k, "") for k in cols})
    buf.seek(0)
    return buf, cols


def _copy(cur, table: str, cols: list[str], buf: io.StringIO,
          force_null: bool = True) -> int:
    """COPY a staging buffer into `table` (H-5 bulk path). FORCE_NULL turns
    empty CSV fields into SQL NULL for the listed columns."""
    col_sql = ", ".join(cols)
    fn = f", FORCE_NULL ({col_sql})" if force_null else ""
    cur.copy_expert(
        f"COPY {table} ({col_sql}) FROM STDIN WITH (FORMAT csv, HEADER true{fn})",
        buf,
    )
    return cur.rowcount


def main() -> None:
    started = dt.datetime.now(dt.timezone.utc)
    print(f"[loader] start at {started.isoformat()} (psycopg2, single txn)")

    focus_buf, focus_cols = _build_focus_staging()
    join_buf, join_cols = _build_join_staging()
    util_buf, util_cols = _build_util_staging()

    conn = psycopg2.connect(**_conn_kwargs())
    try:
        conn.autocommit = False          # H-4: one transaction
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE focus_costs, resource_join_map, miq_utilization, "
                "miq_onprem_cost RESTART IDENTITY"
            )
            n_focus = _copy(cur, "focus_costs", focus_cols, focus_buf)
            n_join = _copy(cur, "resource_join_map", join_cols, join_buf)
            # util has no nullable empties to force; keep FORCE_NULL off
            n_util = _copy(cur, "miq_utilization", util_cols, util_buf, force_null=False)

            finished = dt.datetime.now(dt.timezone.utc)
            cur.execute(
                "INSERT INTO load_metadata "
                "(started_at, finished_at, focus_rows_loaded, join_rows_loaded, "
                " miq_util_rows_loaded, miq_onprem_rows_loaded, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (started, finished, n_focus, n_join, n_util, 0,
                 "psycopg2 single-txn load; onprem loaded separately"),
            )
        conn.commit()                    # H-4: all-or-nothing
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"[loader] focus_costs: {n_focus} rows")
    print(f"[loader] resource_join_map: {n_join} rows")
    print(f"[loader] miq_utilization: {n_util} rows")
    print(f"[loader] committed in {(finished - started).total_seconds():.2f}s")


if __name__ == "__main__":
    main()
