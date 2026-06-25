"""Map Azure cost-export rows to FOCUS v1.3 columns.

This is the leading-edge mapping per SPEC s2 because Azure is the team's
real near-term pain. Every mapping decision lives here, alongside the
validation that catches the SPEC s3.1 messiness recipes the generator
inserted.

Output rows are dicts keyed by FOCUS v1.3 column display names.
Validation produces a parallel report dict per row so the loader can
emit a row-by-row diagnostic without losing the underlying datum.
"""
from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import json
import os
import sys
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalizer import focus_spec


# Azure-specific category-string mappings. These are the strings Azure emits
# (MeterCategory / ServiceFamily) and the FOCUS v1.3 ServiceCategory each
# maps to. Anything unrecognized maps to "Other" with a validation warning.
#
# Note the '&' vs 'and' difference between Azure ('AI + Machine Learning')
# and FOCUS ('AI and Machine Learning') --- a real source of normalizer bugs.
AZURE_SERVICE_FAMILY_TO_FOCUS = {
    "Compute": "Compute",
    "Storage": "Storage",
    "Networking": "Networking",
    "Databases": "Databases",
    "AI + Machine Learning": "AI and Machine Learning",
    "Analytics": "Analytics",
    "Security": "Security",
    "Identity": "Identity",
    "Integration": "Integration",
    "Management and Governance": "Management and Governance",
    "Internet of Things": "Internet of Things",
    "Containers": "Compute",                  # FOCUS rolls containers under Compute
    "Web": "Web",
    "Developer Tools": "Developer Tools",
    "Migration": "Migration",
    "Other": "Other",
}

# Azure ChargeType -> FOCUS ChargeCategory
AZURE_CHARGE_TYPE_TO_FOCUS = {
    "Usage": "Usage",
    "Purchase": "Purchase",
    "Refund": "Credit",
    "Adjustment": "Adjustment",
    "Tax": "Tax",
}


@dataclasses.dataclass
class MappedRow:
    focus_row: dict[str, object]
    warnings: list[str]  # per-row diagnostic; empty list = clean
    fatal: bool = False  # True if the row is unsalvageable (e.g. no cost)


def _parse_arm_resource_type(arm_path: str) -> str:
    """Extract the Resource Type from an Azure ARM path.

    e.g. '/subscriptions/.../providers/Microsoft.Compute/virtualMachines/foo'
         -> 'Microsoft.Compute/virtualMachines'
    """
    if not arm_path:
        return ""
    if "/providers/" not in arm_path:
        return ""
    after = arm_path.split("/providers/", 1)[1]
    parts = after.split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return after


def map_row(az: dict[str, str]) -> MappedRow:
    """Map one Azure cost-export row to a FOCUS v1.3 row dict."""
    warnings: list[str] = []
    fatal = False

    # --- ServiceCategory (mandatory, closed set) ---
    azure_family = az.get("ServiceFamily", "").strip()
    if not azure_family:
        warnings.append("ServiceFamily blank --- mapped ServiceCategory to 'Other'")
        focus_category = "Other"
    elif azure_family in AZURE_SERVICE_FAMILY_TO_FOCUS:
        focus_category = AZURE_SERVICE_FAMILY_TO_FOCUS[azure_family]
    else:
        warnings.append(
            f"ServiceFamily {azure_family!r} not in mapping table --- defaulted to 'Other'"
        )
        focus_category = "Other"

    if focus_category not in focus_spec.SERVICE_CATEGORIES_V1_3:
        # programmer error: the mapping table itself is wrong
        warnings.append(
            f"INTERNAL: mapped ServiceCategory {focus_category!r} is not in the "
            f"FOCUS v1.3 closed set --- normalizer bug"
        )
        focus_category = "Other"

    # --- Tags (Azure ships them as a JSON-string-in-a-cell) ---
    tags_raw = az.get("Tags", "") or ""
    tags: dict[str, str] = {}
    if tags_raw:
        try:
            parsed = json.loads(tags_raw)
            if isinstance(parsed, dict):
                tags = {str(k): str(v) for k, v in parsed.items()}
            else:
                warnings.append(f"Tags JSON parsed to non-object: {type(parsed).__name__}")
        except json.JSONDecodeError as e:
            warnings.append(f"Tags JSON parse failed: {e}; left as empty dict")

    # --- Cost & currency ---
    try:
        billed_cost = float(az.get("CostInBillingCurrency", "") or 0)
    except ValueError:
        warnings.append("CostInBillingCurrency unparseable; coerced to 0.0")
        billed_cost = 0.0
    try:
        priced_cost = float(az.get("CostInPricingCurrency", "") or 0)
    except ValueError:
        warnings.append("CostInPricingCurrency unparseable; coerced to 0.0")
        priced_cost = 0.0

    # --- Periods ---
    charge_start_str = az.get("Date", "")
    try:
        charge_start = dt.datetime.fromisoformat(charge_start_str).replace(tzinfo=dt.timezone.utc)
    except (ValueError, TypeError):
        warnings.append(f"Date {charge_start_str!r} unparseable")
        charge_start = None
    charge_end = charge_start + dt.timedelta(days=1) if charge_start else None

    # --- ChargeCategory ---
    azure_ct = az.get("ChargeType", "Usage")
    charge_category = AZURE_CHARGE_TYPE_TO_FOCUS.get(azure_ct, "Other")
    if azure_ct not in AZURE_CHARGE_TYPE_TO_FOCUS:
        warnings.append(f"ChargeType {azure_ct!r} not mapped --- defaulted to 'Other'")

    # --- ResourceId / ResourceType (Azure ARM-path quirk) ---
    arm_path = az.get("ResourceId", "") or ""
    if arm_path and not arm_path.startswith("/subscriptions/"):
        warnings.append(
            f"ResourceId {arm_path[:60]!r} doesn't look like an ARM path "
            f"--- join with MIQ.ems_ref may fail"
        )
    resource_type = _parse_arm_resource_type(arm_path)

    focus_row: dict[str, object] = {
        "BillingAccountId": az.get("SubscriptionId", ""),
        "BillingAccountName": az.get("SubscriptionName", ""),
        "SubAccountId": az.get("ResourceGroup", ""),
        "SubAccountName": az.get("ResourceGroup", ""),
        "BillingPeriodStart": az.get("BillingPeriodStartDate", ""),
        "BillingPeriodEnd": az.get("BillingPeriodEndDate", ""),
        "ChargePeriodStart": charge_start.isoformat() if charge_start else "",
        "ChargePeriodEnd": charge_end.isoformat() if charge_end else "",
        "ChargeCategory": charge_category,
        "ChargeDescription": az.get("MeterName", ""),
        "ChargeFrequency": "Usage-Based",
        "BilledCost": billed_cost,
        "EffectiveCost": billed_cost,
        "ListCost": priced_cost,                 # USD list price
        "ContractedCost": billed_cost,
        "BillingCurrency": az.get("BillingCurrency", ""),
        "PricingCurrency": az.get("PricingCurrency", ""),
        "ServiceProviderName": "Microsoft",
        "InvoiceIssuerName": "Microsoft",
        "ServiceCategory": focus_category,
        "ServiceSubcategory": az.get("MeterSubCategory", ""),
        "ServiceName": az.get("MeterCategory", ""),
        "SkuId": az.get("MeterId", ""),
        "SkuMeter": az.get("MeterName", ""),
        "SkuPriceId": "",                        # Azure doesn't ship a separate price id
        "ResourceId": arm_path,
        "ResourceName": arm_path.split("/")[-1] if arm_path else "",
        "ResourceType": resource_type,
        "RegionId": az.get("ResourceLocation", ""),
        "RegionName": az.get("ResourceLocation", ""),
        "AvailabilityZone": "",                  # Azure cost-export doesn't carry AZ
        "ConsumedQuantity": float(az.get("Quantity", 0) or 0),
        "ConsumedUnit": az.get("UnitOfMeasure", ""),
        "PricingQuantity": float(az.get("Quantity", 0) or 0),
        "PricingUnit": az.get("UnitOfMeasure", ""),
        "Tags": json.dumps(tags, separators=(",", ":")),
    }

    # --- Final mandatory-field checks (FOCUS conformance) ---
    if not focus_row["ServiceCategory"] or focus_row["ServiceCategory"] not in focus_spec.SERVICE_CATEGORIES_V1_3:
        warnings.append("ServiceCategory failed final conformance check")
        fatal = True
    if not focus_row["BillingCurrency"]:
        warnings.append("BillingCurrency is empty --- FOCUS requires this")
        fatal = True

    return MappedRow(focus_row=focus_row, warnings=warnings, fatal=fatal)


def normalize_csv(input_csv_path: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Read the Azure CSV, return (focus_rows, validation_report).

    The validation report is one row per source row, carrying:
       row_index, fatal, warnings list, original Azure row (small subset).
    """
    focus_rows: list[dict[str, object]] = []
    report: list[dict[str, object]] = []
    with open(input_csv_path) as f:
        reader = csv.DictReader(f)
        for idx, az_row in enumerate(reader):
            mapped = map_row(az_row)
            report.append({
                "row_index": idx,
                "fatal": mapped.fatal,
                "warnings": mapped.warnings,
                "source_resource_id": az_row.get("ResourceId", "")[:80],
                "source_meter_category": az_row.get("MeterCategory", ""),
                "source_service_family": az_row.get("ServiceFamily", ""),
            })
            if not mapped.fatal:
                focus_rows.append(mapped.focus_row)
    return focus_rows, report


if __name__ == "__main__":
    in_path = sys.argv[1] if len(sys.argv) > 1 else "out/generators/azure_cost.csv"
    rows, report = normalize_csv(in_path)
    print(f"input:           {in_path}")
    print(f"rows mapped:     {len(rows)}")
    print(f"rows dropped:    {sum(1 for r in report if r['fatal'])}")
    print(f"rows with warns: {sum(1 for r in report if r['warnings'] and not r['fatal'])}")
    print()
    print("Sample warnings:")
    for r in report:
        if r["warnings"]:
            print(f"  row {r['row_index']}: {r['warnings']}")
    print()
    print("Distinct ServiceCategory values emitted:",
          sorted({r["ServiceCategory"] for r in rows}))
