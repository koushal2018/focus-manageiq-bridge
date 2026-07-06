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


def run(only_source_id: str | None = None, out_csv: str | None = None) -> dict:
    """Dispatch registered sources to FOCUS rows.

    only_source_id: if given, process ONLY that source (incremental upload path,
      W-15) — its rows are written to a per-source CSV so the combined file and
      other sources' partitions are untouched. The returned dict carries
      `out_csv` (the path written) so the caller can do a per-source DB load.
    out_csv: override the output path (defaults to the combined CSV, or a
      per-source file when only_source_id is set).
    """
    sources = [s for s in registry.load() if s.enabled]
    if only_source_id is not None:
        sources = [s for s in sources if s.source_id == only_source_id]
    all_rows: list[dict] = []
    all_report: list[dict] = []
    summary: list[dict] = []

    target_csv = out_csv or (
        os.path.join(OUT_DIR, f"focus_source_{only_source_id}.csv")
        if only_source_id is not None else FOCUS_CSV)

    print(f"[dispatch] {len(sources)} enabled source(s)"
          + (f" (only {only_source_id})" if only_source_id else " in registry"))
    for cfg in sources:
        adapter = ADAPTERS.get(cfg.source_type)
        if adapter is None:
            print(f"[dispatch] {cfg.source_id}: no adapter for type "
                  f"{cfg.source_type!r} — skipped")
            summary.append({"source_id": cfg.source_id, "status": "no-adapter"})
            continue

        # discover() is INSIDE the fail-soft boundary: a stubbed api-pull source
        # raises NotImplementedError from discover(), and one such source must
        # not sink the whole run (it would 500 every /connect action and abort
        # the seed → restart loop). Accumulate this source's rows in a LOCAL
        # buffer and only commit them to all_rows if the ENTIRE source succeeds —
        # a mid-file normalize error must not leave a partial slice that a
        # partition-replace load would treat as the source's full data.
        try:
            exports = adapter.discover(cfg)
            if not exports:
                print(f"[dispatch] {cfg.source_id}: no exports found at "
                      f"{cfg.location!r}")
                summary.append({"source_id": cfg.source_id, "status": "no-exports"})
                continue
            src_buf: list[dict] = []
            src_report: list[dict] = []
            src_rows = 0
            for exp in exports:
                result = adapter.normalize(cfg, exp)
                for r in result.focus_rows:
                    r["_source_id"] = cfg.source_id
                src_buf.extend(result.focus_rows)
                src_report.extend(result.report)
                src_rows += result.loaded
        except Exception as e:  # one poison source must not sink the run
            print(f"[dispatch] {cfg.source_id}: dispatch error: {e}")
            summary.append({"source_id": cfg.source_id, "status": "error",
                            "error": str(e)})
            continue
        # Source fully succeeded — now commit its rows atomically.
        all_rows.extend(src_buf)
        all_report.extend(src_report)
        print(f"[dispatch] {cfg.source_id:18s} [{cfg.source_type:13s}] "
              f"-> {src_rows} FOCUS rows")
        summary.append({"source_id": cfg.source_id, "status": "ok", "rows": src_rows})

    # Write the FOCUS CSV — identical shape to the PoC normalizer output, so
    # db/loader.py consumes it unchanged. `_source_id` is now persisted so the
    # loader can do per-source partition replace (W-15).
    os.makedirs(OUT_DIR, exist_ok=True)
    # Carry _extensions (provider x_ columns folded to JSON by the native
    # adapter, H-9) through to the loader.
    columns = ["_source", "_source_id"] + focus_spec.FOCUS_COLUMNS_V1_3 + ["_extensions"]
    with open(target_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in columns})
    # Write the validation report to a per-source path when this is a
    # single-source (upload) run, so it does NOT clobber the full-run report
    # that covers every source with just this upload's handful of rows.
    report_json = (
        os.path.join(OUT_DIR, f"validation_report_{only_source_id}.json")
        if only_source_id is not None else REPORT_JSON)
    with open(report_json, "w") as f:
        json.dump(all_report, f, indent=2, default=str)

    distinct = sorted({r.get("ServiceCategory", "") for r in all_rows})
    invalid = [c for c in distinct if c and c not in focus_spec.SERVICE_CATEGORIES_V1_3]

    print(f"[dispatch] wrote {len(all_rows)} FOCUS rows -> {target_csv}")
    print(f"[dispatch] distinct ServiceCategory: {distinct}")
    if invalid:
        print(f"[dispatch] !!! non-conformant categories: {invalid}")

    # `errored` lets a caller tell "this source produced no rows because it was
    # already loaded" (safe no-op) apart from "this source FAILED to dispatch"
    # (must not be treated as an empty-but-successful load — that would let a
    # partition-replace wipe good data on a false success).
    errored = [s for s in summary if s.get("status") == "error"]
    return {"sources": summary, "focus_rows": len(all_rows),
            "nonconformant_categories": invalid, "out_csv": target_csv,
            "errored": errored}


if __name__ == "__main__":
    run()
