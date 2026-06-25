"""Run all three normalizers and emit a single combined FOCUS CSV +
validation report. The join slice reads this output.

Usage:
    python3 -m normalizer
"""
from __future__ import annotations

import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalizer import aws_to_focus, azure_to_focus, focus_spec, oci_to_focus


def main() -> None:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(here, "out", "normalizer")
    os.makedirs(out_dir, exist_ok=True)

    sources = [
        ("aws",   "out/generators/aws_cur.csv",    aws_to_focus.normalize_csv),
        ("azure", "out/generators/azure_cost.csv", azure_to_focus.normalize_csv),
        ("oci",   "out/generators/oci_usage.csv",  oci_to_focus.normalize_csv),
    ]

    all_rows: list[dict[str, object]] = []
    all_report: list[dict[str, object]] = []

    for label, path, fn in sources:
        abs_path = os.path.join(here, path)
        rows, report = fn(abs_path)
        for r in rows:
            r["_source"] = label  # private debug field; strip before publishing
        for r in report:
            r["_source"] = label
        all_rows.extend(rows)
        all_report.extend(report)
        print(
            f"{label:6s}: {len(rows):4d} rows, "
            f"{sum(1 for x in report if x['fatal'])} fatal, "
            f"{sum(1 for x in report if x['warnings'] and not x['fatal'])} warnings"
        )

    # Write the combined FOCUS CSV
    focus_path = os.path.join(out_dir, "focus_combined.csv")
    columns = ["_source"] + focus_spec.FOCUS_COLUMNS_V1_3
    with open(focus_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in columns})
    print(f"wrote {focus_path} ({len(all_rows)} rows)")

    # Write the validation report (JSON --- easier to inspect)
    report_path = os.path.join(out_dir, "validation_report.json")
    with open(report_path, "w") as f:
        json.dump(all_report, f, indent=2, default=str)
    print(f"wrote {report_path}")

    # Sanity print: distinct ServiceCategory across all sources
    distinct = sorted({r["ServiceCategory"] for r in all_rows})
    print(f"distinct ServiceCategory: {distinct}")
    invalid = [c for c in distinct if c not in focus_spec.SERVICE_CATEGORIES_V1_3]
    if invalid:
        print(f"!!! FOCUS conformance failure: {invalid} not in spec closed set")
        sys.exit(1)


if __name__ == "__main__":
    main()
