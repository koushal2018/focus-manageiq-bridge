"""Materialize resource_join_map: the FOCUS<->ManageIQ identity join.

This is the slice the WHOLE POC IS ABOUT (per SPEC s2 build order: the join
is the first landmine). The job here is NOT to maximize matches --- it's to
expose every failure mode so the EBA team sees them BEFORE production.

Strategy per GOTCHA J-1:
  - AWS rows join on focus.ResourceId == vms.uid_ems   (both i-...)
  - OCI rows join on focus.ResourceId == vms.uid_ems   (both ocid1...)
  - Azure rows join on focus.ResourceId == vms.ems_ref (both ARM paths)

Each focus row's match status is one of:
  matched              --- exactly one MIQ VM found
  unmatched_focus_only --- cost row exists, no MIQ VM (untracked cloud resource?)
  ambiguous            --- multiple MIQ VMs match (cross-cloud same workload)
  no_resource_id       --- the focus row has no ResourceId (e.g. tax/refund)

Each MIQ VM that is NOT matched by any focus row gets a row of its own
with status 'unmatched_miq_only' --- the on-prem-only case from SPEC s3.1.

Output: out/join/resource_join_map.csv + a printed summary.
"""
from __future__ import annotations

import csv
import dataclasses
import os
import sys
from collections import defaultdict
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from join import miq_client


@dataclasses.dataclass
class JoinRow:
    status: str
    focus_source: str               # 'aws'/'azure'/'oci' or '' for MIQ-only
    focus_resource_id: str
    focus_service_category: str
    focus_billed_cost_sum: float
    focus_row_count: int
    miq_vm_id: str
    miq_vm_name: str
    miq_vendor: str
    miq_uid_ems: str
    miq_ems_ref: str
    join_key_used: str              # 'uid_ems' / 'ems_ref' / '' (no match)
    notes: str


def _load_focus_rows(path: str) -> list[dict]:
    with open(path) as f:
        return list(csv.DictReader(f))


def _miq_key_for_provider(source: str) -> str:
    """Per GOTCHA J-1: Azure joins on ems_ref, AWS/OCI on uid_ems."""
    return "ems_ref" if source == "azure" else "uid_ems"


def build(
    focus_csv_path: str,
    miq_vms: list[dict] | None = None,
) -> list[JoinRow]:
    focus_rows = _load_focus_rows(focus_csv_path)
    if miq_vms is None:
        miq_vms = miq_client.get_vms()

    # Aggregate focus rows by (source, resource_id) to collapse 24h of EC2
    # hourly rows into one join key. We sum BilledCost across the group so
    # the join row carries something meaningful.
    grouped: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"rows": 0, "cost": 0.0, "categories": set()}
    )
    for r in focus_rows:
        source = r.get("_source", "")
        rid = r.get("ResourceId", "")
        key = (source, rid)
        try:
            cost = float(r.get("BilledCost", "") or 0)
        except ValueError:
            cost = 0.0
        grouped[key]["rows"] += 1
        grouped[key]["cost"] += cost
        grouped[key]["categories"].add(r.get("ServiceCategory", ""))

    # Index MIQ vms by uid_ems and ems_ref for O(1) lookup
    by_uid_ems: dict[str, list[dict]] = defaultdict(list)
    by_ems_ref: dict[str, list[dict]] = defaultdict(list)
    for vm in miq_vms:
        if vm.get("uid_ems"):
            by_uid_ems[vm["uid_ems"]].append(vm)
        if vm.get("ems_ref"):
            by_ems_ref[vm["ems_ref"]].append(vm)

    matched_vm_ids: set[str] = set()
    join_rows: list[JoinRow] = []

    for (source, rid), agg in sorted(grouped.items()):
        common = {
            "focus_source": source,
            "focus_resource_id": rid,
            "focus_service_category": " | ".join(sorted(c for c in agg["categories"] if c)),
            "focus_billed_cost_sum": round(agg["cost"], 6),
            "focus_row_count": agg["rows"],
        }
        if not rid:
            join_rows.append(JoinRow(
                status="no_resource_id",
                miq_vm_id="", miq_vm_name="", miq_vendor="",
                miq_uid_ems="", miq_ems_ref="",
                join_key_used="",
                notes=(
                    "FOCUS row carries no ResourceId --- typical for tax, "
                    "refund, support, or tenancy-level charges"
                ),
                **common,
            ))
            continue

        key_col = _miq_key_for_provider(source)
        candidates = (by_ems_ref if key_col == "ems_ref" else by_uid_ems).get(rid, [])

        if not candidates:
            join_rows.append(JoinRow(
                status="unmatched_focus_only",
                miq_vm_id="", miq_vm_name="", miq_vendor="",
                miq_uid_ems="", miq_ems_ref="",
                join_key_used=key_col,
                notes=(
                    f"No MIQ vm found via vms.{key_col} = {rid[:60]}. "
                    "Untracked cloud resource, or refresh hasn't run, "
                    "or the join key is wrong for this provider."
                ),
                **common,
            ))
        elif len(candidates) == 1:
            vm = candidates[0]
            matched_vm_ids.add(vm["id"])
            join_rows.append(JoinRow(
                status="matched",
                miq_vm_id=str(vm["id"]),
                miq_vm_name=vm.get("name", ""),
                miq_vendor=vm.get("vendor", ""),
                miq_uid_ems=vm.get("uid_ems", "") or "",
                miq_ems_ref=vm.get("ems_ref", "") or "",
                join_key_used=key_col,
                notes="",
                **common,
            ))
        else:
            for vm in candidates:
                matched_vm_ids.add(vm["id"])
            join_rows.append(JoinRow(
                status="ambiguous",
                miq_vm_id=",".join(str(v["id"]) for v in candidates),
                miq_vm_name=" | ".join(v.get("name", "") for v in candidates),
                miq_vendor=" | ".join(v.get("vendor", "") for v in candidates),
                miq_uid_ems=" | ".join((v.get("uid_ems") or "") for v in candidates),
                miq_ems_ref=" | ".join((v.get("ems_ref") or "") for v in candidates),
                join_key_used=key_col,
                notes=(
                    f"{len(candidates)} MIQ vms share this ResourceId. "
                    "Likely the same workload visible to multiple providers."
                ),
                **common,
            ))

    # Now emit MIQ-only rows for every VM nothing matched
    for vm in miq_vms:
        if vm["id"] in matched_vm_ids:
            continue
        # Skip the SmartProxy/system VMs the appliance might have
        join_rows.append(JoinRow(
            status="unmatched_miq_only",
            focus_source="",
            focus_resource_id="",
            focus_service_category="",
            focus_billed_cost_sum=0.0,
            focus_row_count=0,
            miq_vm_id=str(vm["id"]),
            miq_vm_name=vm.get("name", ""),
            miq_vendor=vm.get("vendor", ""),
            miq_uid_ems=vm.get("uid_ems", "") or "",
            miq_ems_ref=vm.get("ems_ref", "") or "",
            join_key_used="",
            notes=(
                "MIQ vm with no matching FOCUS row. Either on-prem "
                "(SPEC s3.1 'no cloud ResourceId') or cloud-side data missing."
            ),
        ))

    return join_rows


def write_csv(rows: list[JoinRow], path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cols = [f.name for f in dataclasses.fields(JoinRow)]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(dataclasses.asdict(r))
    return path


def summarize(rows: list[JoinRow]) -> None:
    by_status: dict[str, int] = defaultdict(int)
    for r in rows:
        by_status[r.status] += 1
    print("Join status summary:")
    for s, n in sorted(by_status.items()):
        print(f"  {s:<22} {n}")
    print()

    matched = sum(r.focus_billed_cost_sum for r in rows if r.status == "matched")
    focus_only = sum(r.focus_billed_cost_sum for r in rows if r.status == "unmatched_focus_only")
    print(f"BilledCost attributed to matched MIQ vms : {matched:>12,.2f}")
    print(f"BilledCost stranded as focus-only        : {focus_only:>12,.2f}")
    print()

    print("Unmatched MIQ-only (on-prem candidates):")
    for r in rows:
        if r.status == "unmatched_miq_only":
            print(f"  vm {r.miq_vm_id} {r.miq_vm_name!r} vendor={r.miq_vendor!r}")
    print()

    print("Focus-only (cost with no MIQ inventory):")
    shown = 0
    for r in rows:
        if r.status == "unmatched_focus_only" and shown < 10:
            print(
                f"  {r.focus_source:5s} cost={r.focus_billed_cost_sum:>10.2f} "
                f"rows={r.focus_row_count} rid={r.focus_resource_id[:60]}"
            )
            shown += 1
    if shown == 10:
        rest = sum(1 for r in rows if r.status == "unmatched_focus_only") - 10
        print(f"  ... and {rest} more")


if __name__ == "__main__":
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows = build(os.path.join(here, "out/normalizer/focus_combined.csv"))
    out_path = os.path.join(here, "out/join/resource_join_map.csv")
    write_csv(rows, out_path)
    print(f"wrote {out_path}")
    print()
    summarize(rows)
