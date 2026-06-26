"""One-shot seed: build synthetic data and load it into the FOCUS DB.

Runs the full PoC pipeline in order, idempotently:
  generators (AWS/Azure/OCI CSVs)
   -> MIQ snapshot (vms.json + metric_rollups.json)
   -> connector dispatcher (registry-driven -> focus_combined.csv)
   -> resource_join_map (reads MIQ snapshot)
   -> db.loader (focus_costs + resource_join_map + miq_utilization)
   -> onprem.cost_model --load (miq_onprem_cost)

Honors FOCUS_PG_* env so it targets whatever Postgres the compose/k8s
environment provides. Safe to re-run: every step truncates+reloads.

This is the container entrypoint's data step; the web process starts after
it returns 0.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main() -> int:
    # 1. generators — provider-native synthetic exports
    from generators import aws_cur, azure_cost_export, oci_usage
    aws_cur.write_csv()
    azure_cost_export.write_csv()
    oci_usage.write_csv()
    print("[seed] generators: wrote AWS/Azure/OCI CSVs")

    # 2. MIQ inventory + utilization snapshot (appliance retired, LM-1)
    from join import miq_snapshot
    vms_path, util_path = miq_snapshot.write_snapshots()
    print(f"[seed] miq snapshot: {vms_path}, {util_path}")

    # 3. connector dispatcher — registry-driven normalize to FOCUS
    from connectors import dispatcher
    disp = dispatcher.run()
    print(f"[seed] dispatcher: {disp['focus_rows']} FOCUS rows")

    # 4. join (reads the MIQ snapshot via env)
    os.environ.setdefault("MIQ_VMS_JSON", vms_path)
    from join import resource_join_map
    rows = resource_join_map.build(
        os.path.join(ROOT, "out", "normalizer", "focus_combined.csv")
    )
    resource_join_map.write_csv(
        rows, os.path.join(ROOT, "out", "join", "resource_join_map.csv")
    )
    print(f"[seed] join: {len(rows)} resource_join_map rows")

    # 5. load FOCUS + join + utilization into Postgres
    from db import loader
    loader.main()

    # 6. on-prem recharge rows
    from onprem import cost_model
    n = cost_model.load_into_postgres()
    print(f"[seed] onprem: {n} miq_onprem_cost rows")

    print("[seed] complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
