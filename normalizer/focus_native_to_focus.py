"""Normalize a NATIVE FOCUS export to the platform's FOCUS v1.3 target.

This is the post-NF-1 path: the providers now emit FOCUS directly (AWS/Azure
1.2, OCI 1.0), so there is no CUR/cost-export parsing to do. The work here is:

  1. **Near-identity** — the columns already ARE FOCUS; keep the ones in our
     target schema.
  2. **Version-leveling** — OCI ships 1.0, AWS/Azure 1.2; we target a single
     internal version (the FOCUS_COLUMNS_V1_3 set). Missing columns are left
     blank, not invented.
  3. **Gap-fill / validation** — ServiceCategory must be non-null and in the
     closed set (F-2); rows failing this are reported, not silently loaded.
  4. **Drop x_ extension columns** — provider-proprietary columns (x_Discounts
     etc.) are NOT part of portable FOCUS. We drop them here and RECORD that we
     did (GOTCHA H-9). A production build would preserve high-value ones into a
     dedicated column / JSONB blob.

Contract mirrors the other normalizers: normalize_csv(path) -> (rows, report).
"""
from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalizer import focus_spec

TARGET_COLUMNS = focus_spec.FOCUS_COLUMNS_V1_3


def map_row(row: dict[str, str]) -> tuple[dict[str, object], list[str]]:
    """Project a native-FOCUS row onto the target FOCUS column set."""
    warnings: list[str] = []

    # Drop x_ provider extensions (H-9), note which were present.
    x_cols = [k for k in row if k.startswith("x_")]
    if x_cols:
        warnings.append(f"dropped provider extension columns: {sorted(x_cols)}")

    out: dict[str, object] = {}
    for col in TARGET_COLUMNS:
        out[col] = row.get(col, "")

    # ServiceCategory conformance (F-2): mandatory, closed set.
    cat = (out.get("ServiceCategory") or "").strip()
    fatal = False
    if not cat:
        warnings.append("ServiceCategory is empty (FOCUS mandates non-null)")
        fatal = True
    elif cat not in focus_spec.SERVICE_CATEGORIES_V1_3:
        warnings.append(f"ServiceCategory {cat!r} not in FOCUS closed set")
        fatal = True

    # BillingCurrency is mandatory in FOCUS too.
    if not (out.get("BillingCurrency") or "").strip():
        warnings.append("BillingCurrency is empty (FOCUS mandates non-null)")
        fatal = True

    out["_fatal"] = fatal
    return out, warnings


def normalize_csv(input_csv_path: str) -> tuple[list[dict], list[dict]]:
    focus_rows: list[dict] = []
    report: list[dict] = []
    with open(input_csv_path) as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            mapped, warnings = map_row(row)
            fatal = mapped.pop("_fatal")
            report.append({
                "row_index": idx,
                "fatal": fatal,
                "warnings": warnings,
                "source_resource_id": (row.get("ResourceId") or "")[:80],
                "source_service_category": row.get("ServiceCategory", ""),
            })
            if not fatal:
                focus_rows.append(mapped)
    return focus_rows, report


if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv) > 1 else "out/generators/focus_aws.csv"
    rows, rep = normalize_csv(p)
    print(f"input: {p}")
    print(f"mapped: {len(rows)} | dropped(fatal): {sum(1 for r in rep if r['fatal'])}")
    drops = [r for r in rep if r["warnings"]]
    print(f"rows with warnings: {len(drops)}")
    if drops:
        print("first warning:", drops[0]["warnings"])
