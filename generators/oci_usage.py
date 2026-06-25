"""Generate a synthetic OCI usage report CSV.

OCI's cost & usage CSV uses **lineItem/...** column prefixes (similar look
to AWS CUR but with OCI-specific naming: tenantId, compartmentId, productSku),
OCID identifiers for resources, and an `unitPrice` rather than a separate
pricing-currency cost.

Messiness for SPEC s3.1:
  - **Hyphenated regions** (`me-dubai-1`) where AWS uses `me-central-1` ---
    the same physical city named differently is its own join landmine.
  - **`product/Description` is free-text** with marketing strings ('Compute
    Standard - Skylake') --- the normalizer must rely on `product/sku`
    instead.
  - A row with **empty compartmentId** --- common when a billing record
    spans a tenancy-level service.

Output: out/generators/oci_usage.csv
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators import common

# OCI cost-and-usage report columns. Real OCI exports have ~40; this is the
# representative subset.
OCI_COLUMNS = [
    "lineItem/intervalUsageStart",
    "lineItem/intervalUsageEnd",
    "lineItem/referenceNo",
    "lineItem/tenantId",
    "lineItem/compartmentId",
    "lineItem/compartmentName",
    "lineItem/compartmentPath",
    "lineItem/region",
    "lineItem/availabilityDomain",
    "lineItem/resourceId",
    "lineItem/resourceName",
    "product/service",
    "product/resource",
    "product/sku",
    "product/Description",
    "usage/billedQuantity",
    "usage/billedQuantityOverage",
    "usage/consumedQuantity",
    "usage/consumedQuantityUnits",
    "cost/subscriptionId",
    "cost/productSku",
    "cost/unitPrice",
    "cost/unitPriceOverage",
    "cost/myCost",
    "cost/myCostOverage",
    "cost/currencyCode",
    "cost/billingUnitReadable",
    "tags/Oracle-Tags.CreatedBy",
    "tags/orcl-cloud.free-tier-retained",
    "tags/user.app",
    "tags/user.env",
]


def _row(
    *,
    start: dt.datetime,
    end: dt.datetime,
    line_no: int,
    compartment_name: str,
    region: str,
    resource_id: str,
    resource_name: str,
    service: str,
    resource_type: str,
    sku: str,
    description: str,
    billed_quantity: float,
    consumed_quantity: float,
    consumed_units: str,
    unit_price_usd: float,
    cost_usd: float,
    tags_user: dict[str, str] | None = None,
    empty_compartment: bool = False,
) -> dict[str, str]:
    tags_user = tags_user or {}
    return {
        "lineItem/intervalUsageStart": start.isoformat(),
        "lineItem/intervalUsageEnd": end.isoformat(),
        "lineItem/referenceNo": f"ocr-demo-{line_no:08d}",
        "lineItem/tenantId": common.FAKE_OCI_TENANCY,
        "lineItem/compartmentId": (
            "" if empty_compartment
            else f"ocid1.compartment.oc1..demo{compartment_name.lower()}"
        ),
        "lineItem/compartmentName": "" if empty_compartment else compartment_name,
        "lineItem/compartmentPath": (
            "" if empty_compartment else f"DEMO-Tenancy/{compartment_name}"
        ),
        "lineItem/region": region,
        "lineItem/availabilityDomain": f"AD-{region}-1",
        "lineItem/resourceId": resource_id,
        "lineItem/resourceName": resource_name,
        "product/service": service,
        "product/resource": resource_type,
        "product/sku": sku,
        "product/Description": description,
        "usage/billedQuantity": f"{billed_quantity:.6f}",
        "usage/billedQuantityOverage": "0",
        "usage/consumedQuantity": f"{consumed_quantity:.6f}",
        "usage/consumedQuantityUnits": consumed_units,
        "cost/subscriptionId": "sub-demo-0001",
        "cost/productSku": sku,
        "cost/unitPrice": f"{unit_price_usd:.6f}",
        "cost/unitPriceOverage": "0",
        "cost/myCost": f"{cost_usd:.6f}",
        "cost/myCostOverage": "0",
        "cost/currencyCode": "USD",
        "cost/billingUnitReadable": consumed_units,
        "tags/Oracle-Tags.CreatedBy": "demo-loader",
        "tags/orcl-cloud.free-tier-retained": "false",
        "tags/user.app": tags_user.get("app", ""),
        "tags/user.env": tags_user.get("env", ""),
    }


def generate(days: int = 3) -> list[dict[str, str]]:
    rng = common.make_rng()
    rows: list[dict[str, str]] = []
    oci_workloads = [w for w in common.WORKLOADS if w.oci_resource_id]
    n = 0

    # OCI compute hourly for each OCI workload
    for start, end in common.hourly_periods(days):
        for wl in oci_workloads:
            n += 1
            base = 0.04 * wl.cpu_cores + 0.0008 * (wl.memory_mb / 1024)
            cost = max(0.0, base + rng.uniform(-0.003, 0.003))
            rows.append(
                _row(
                    start=start,
                    end=end,
                    line_no=n,
                    compartment_name=wl.business_unit.title().replace("-", ""),
                    region=common.OCI_REGIONS[0],
                    resource_id=wl.oci_resource_id or "",
                    resource_name=wl.name_in_provider("oci"),
                    service="COMPUTE",
                    resource_type="VM_STANDARD",
                    sku="B91449",
                    description=f"Compute Standard - Skylake VM.Standard{wl.cpu_cores}.{wl.memory_mb // 1024}GB",
                    billed_quantity=1.0,
                    consumed_quantity=1.0,
                    consumed_units="HOURS",
                    unit_price_usd=cost,
                    cost_usd=cost,
                    tags_user=wl.tags,
                )
            )

    # A tenancy-level row with empty compartment --- gotcha for joiners
    # that assume every row has a compartment to attribute cost to.
    n += 1
    rows.append(
        _row(
            start=dt.datetime(2026, 6, 1, 0, 0, tzinfo=dt.timezone.utc),
            end=dt.datetime(2026, 6, 1, 1, 0, tzinfo=dt.timezone.utc),
            line_no=n,
            compartment_name="",
            region=common.OCI_REGIONS[0],
            resource_id="",
            resource_name="",
            service="SUPPORT",
            resource_type="PREMIER",
            sku="B91234",
            description="Oracle Cloud Support - Premier",
            billed_quantity=1.0,
            consumed_quantity=1.0,
            consumed_units="MONTH",
            unit_price_usd=300.0,
            cost_usd=300.0,
            empty_compartment=True,
        )
    )

    return rows


def write_csv(path: str | None = None) -> str:
    rows = generate(days=3)
    out = path or os.path.join(common.out_dir(), "oci_usage.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OCI_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return out


if __name__ == "__main__":
    p = write_csv()
    print(f"wrote {p}")
