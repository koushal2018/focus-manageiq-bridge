"""Dispatcher — the worker that turns 'connected sources' into FOCUS rows.

Reads the source registry, runs each enabled source through its registered
adapter (discover → normalize), and writes the combined FOCUS CSV that the
existing `db/loader.py` consumes. In production this runs on a schedule
(EventBridge → ECS task / OpenShift CronJob); for the PoC it's a one-shot
`python -m connectors.dispatcher`.

This is the registry-driven replacement for the PoC's hard-coded
`normalizer/__main__.py`: same output file, but the source list comes from
configuration, not from a list literal in code. That difference IS the
connect-and-run promise.
"""
from __future__ import annotations

import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors import registry
from connectors.adapters import ADAPTERS
from normalizer import focus_spec

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "out", "normalizer")
FOCUS_CSV = os.path.join(OUT_DIR, "focus_combined.csv")
REPORT_JSON = os.path.join(OUT_DIR, "validation_report.json")


def run() -> dict:
    sources = [s for s in registry.load() if s.enabled]
    all_rows: list[dict] = []
    all_report: list[dict] = []
    summary: list[dict] = []

    print(f"[dispatch] {len(sources)} enabled source(s) in registry")
    for cfg in sources:
        adapter = ADAPTERS.get(cfg.source_type)
        if adapter is None:
            print(f"[dispatch] {cfg.source_id}: no adapter for type "
                  f"{cfg.source_type!r} — skipped")
            summary.append({"source_id": cfg.source_id, "status": "no-adapter"})
            continue

        exports = adapter.discover(cfg)
        if not exports:
            print(f"[dispatch] {cfg.source_id}: no exports found at "
                  f"{cfg.location!r}")
            summary.append({"source_id": cfg.source_id, "status": "no-exports"})
            continue

        src_rows = 0
        try:
            for exp in exports:
                result = adapter.normalize(cfg, exp)
                for r in result.focus_rows:
                    r["_source_id"] = cfg.source_id
                all_rows.extend(result.focus_rows)
                all_report.extend(result.report)
                src_rows += result.loaded
        except Exception as e:  # one poison source must not sink the run
            print(f"[dispatch] {cfg.source_id}: normalize error: {e}")
            summary.append({"source_id": cfg.source_id, "status": "error",
                            "error": str(e)})
            continue
        print(f"[dispatch] {cfg.source_id:18s} [{cfg.source_type:13s}] "
              f"-> {src_rows} FOCUS rows")
        summary.append({"source_id": cfg.source_id, "status": "ok", "rows": src_rows})

    # Write the combined FOCUS CSV — identical shape to the PoC normalizer
    # output, so db/loader.py consumes it unchanged.
    os.makedirs(OUT_DIR, exist_ok=True)
    # Carry _extensions (provider x_ columns folded to JSON by the native
    # adapter, H-9) through to the loader.
    columns = ["_source"] + focus_spec.FOCUS_COLUMNS_V1_3 + ["_extensions"]
    with open(FOCUS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in columns})
    with open(REPORT_JSON, "w") as f:
        json.dump(all_report, f, indent=2, default=str)

    distinct = sorted({r.get("ServiceCategory", "") for r in all_rows})
    invalid = [c for c in distinct if c and c not in focus_spec.SERVICE_CATEGORIES_V1_3]

    print(f"[dispatch] wrote {len(all_rows)} FOCUS rows -> {FOCUS_CSV}")
    print(f"[dispatch] distinct ServiceCategory: {distinct}")
    if invalid:
        print(f"[dispatch] !!! non-conformant categories: {invalid}")

    return {"sources": summary, "focus_rows": len(all_rows),
            "nonconformant_categories": invalid}


if __name__ == "__main__":
    run()
