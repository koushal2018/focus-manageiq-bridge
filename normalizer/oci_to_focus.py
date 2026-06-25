"""Map OCI usage-report rows to FOCUS v1.3 columns."""
from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalizer import focus_spec


OCI_SERVICE_TO_FOCUS_CATEGORY = {
    "COMPUTE": "Compute",
    "BLOCK_STORAGE": "Storage",
    "OBJECT_STORAGE": "Storage",
    "NETWORK": "Networking",
    "DATABASE": "Databases",
    "ATP": "Databases",
    "ADW": "Databases",
    "AI_PLATFORM": "AI and Machine Learning",
    "DATA_SCIENCE": "AI and Machine Learning",
    "SUPPORT": "Other",          # support fees aren't a FOCUS service category
    "FAST_CONNECT": "Networking",
    "VCN": "Networking",
    "LOAD_BALANCER": "Networking",
}


@dataclasses.dataclass
class MappedRow:
    focus_row: dict[str, object]
    warnings: list[str]
    fatal: bool = False


def map_row(oci: dict[str, str]) -> MappedRow:
    warnings: list[str] = []
    service = (oci.get("product/service", "") or "").upper().strip()
    if not service:
        focus_category = "Other"
        warnings.append("product/service blank --- ServiceCategory defaulted to 'Other'")
    elif service in OCI_SERVICE_TO_FOCUS_CATEGORY:
        focus_category = OCI_SERVICE_TO_FOCUS_CATEGORY[service]
    else:
        warnings.append(f"product/service {service!r} not mapped --- defaulted to 'Other'")
        focus_category = "Other"

    try:
        cost = float(oci.get("cost/myCost", "") or 0)
    except ValueError:
        warnings.append("cost/myCost unparseable; coerced to 0.0")
        cost = 0.0

    try:
        consumed = float(oci.get("usage/consumedQuantity", "") or 0)
    except ValueError:
        consumed = 0.0

    start_str = oci.get("lineItem/intervalUsageStart", "")
    end_str = oci.get("lineItem/intervalUsageEnd", "")
    try:
        start = dt.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        end = dt.datetime.fromisoformat(end_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        start = end = None

    tags = {}
    for k in ("tags/user.app", "tags/user.env"):
        v = oci.get(k, "")
        if v:
            tags[k.split(".")[-1]] = v

    sub_account = oci.get("lineItem/compartmentId", "") or oci.get("lineItem/tenantId", "")
    if not oci.get("lineItem/compartmentId", ""):
        warnings.append("compartmentId blank --- using tenancy id as SubAccount (tenant-level row)")

    focus_row: dict[str, object] = {
        "BillingAccountId": oci.get("lineItem/tenantId", ""),
        "BillingAccountName": "DEMO-OCI-Tenancy",
        "SubAccountId": sub_account,
        "SubAccountName": oci.get("lineItem/compartmentName", ""),
        "BillingPeriodStart": "",  # OCI usage reports don't ship a billing-period column
        "BillingPeriodEnd": "",
        "ChargePeriodStart": start.isoformat() if start else "",
        "ChargePeriodEnd": end.isoformat() if end else "",
        "ChargeCategory": "Usage",
        "ChargeDescription": oci.get("product/Description", ""),
        "ChargeFrequency": "Usage-Based",
        "BilledCost": cost,
        "EffectiveCost": cost,
        "ListCost": cost,
        "ContractedCost": cost,
        "BillingCurrency": oci.get("cost/currencyCode", "USD"),
        "PricingCurrency": oci.get("cost/currencyCode", "USD"),
        "ServiceProviderName": "Oracle Cloud Infrastructure",
        "InvoiceIssuerName": "Oracle Cloud Infrastructure",
        "ServiceCategory": focus_category,
        "ServiceSubcategory": oci.get("product/resource", ""),
        "ServiceName": service.title().replace("_", " "),
        "SkuId": oci.get("product/sku", ""),
        "SkuMeter": oci.get("product/sku", ""),
        "SkuPriceId": "",
        "ResourceId": oci.get("lineItem/resourceId", ""),
        "ResourceName": oci.get("lineItem/resourceName", ""),
        "ResourceType": oci.get("product/resource", ""),
        "RegionId": oci.get("lineItem/region", ""),
        "RegionName": oci.get("lineItem/region", ""),
        "AvailabilityZone": oci.get("lineItem/availabilityDomain", ""),
        "ConsumedQuantity": consumed,
        "ConsumedUnit": oci.get("usage/consumedQuantityUnits", ""),
        "PricingQuantity": float(oci.get("usage/billedQuantity", "") or 0),
        "PricingUnit": oci.get("cost/billingUnitReadable", ""),
        "Tags": json.dumps(tags, separators=(",", ":")),
    }

    fatal = False
    if focus_row["ServiceCategory"] not in focus_spec.SERVICE_CATEGORIES_V1_3:
        warnings.append("ServiceCategory failed final conformance check")
        fatal = True
    if not focus_row["BillingCurrency"]:
        warnings.append("BillingCurrency is empty")
        fatal = True

    return MappedRow(focus_row=focus_row, warnings=warnings, fatal=fatal)


def normalize_csv(input_csv_path: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    focus_rows: list[dict[str, object]] = []
    report: list[dict[str, object]] = []
    with open(input_csv_path) as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            mapped = map_row(row)
            report.append({
                "row_index": idx,
                "fatal": mapped.fatal,
                "warnings": mapped.warnings,
                "source_resource_id": row.get("lineItem/resourceId", "")[:80],
                "source_service": row.get("product/service", ""),
            })
            if not mapped.fatal:
                focus_rows.append(mapped.focus_row)
    return focus_rows, report


if __name__ == "__main__":
    in_path = sys.argv[1] if len(sys.argv) > 1 else "out/generators/oci_usage.csv"
    rows, report = normalize_csv(in_path)
    print(f"input:           {in_path}")
    print(f"rows mapped:     {len(rows)}")
    print(f"rows dropped:    {sum(1 for r in report if r['fatal'])}")
    print(f"rows with warns: {sum(1 for r in report if r['warnings'] and not r['fatal'])}")
    print()
    print("Distinct ServiceCategory:", sorted({r["ServiceCategory"] for r in rows}))
    print("First 3 warnings:")
    for r in report:
        if r["warnings"]:
            print(f"  row {r['row_index']} [{r['source_service']}]: {r['warnings']}")
            break
