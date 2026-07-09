"""Map AWS CUR2 rows to FOCUS v1.3 columns.

The hardest single decision: ServiceCategory. AWS CUR does NOT ship a
FOCUS-conformant ServiceCategory; we must infer it from lineItem/ProductCode
(e.g. AmazonEC2, AmazonS3, AmazonBedrock). The mapping table here is
authoritative for the products this PoC's generator emits and is intended
to be extended (NOT autodetected --- silent defaulting to 'Other' hides
real categorization gaps).
"""
from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalizer import focus_spec


# AWS lineItem/ProductCode -> FOCUS v1.3 ServiceCategory.
# Hand-curated; covers the products this PoC's CUR generator emits.
# When extending: open the FOCUS spec section 3.1.55.5 and use the *exact*
# allowed-values string.
AWS_PRODUCT_TO_FOCUS_CATEGORY = {
    "AmazonEC2": "Compute",
    "AmazonRDS": "Databases",
    "AmazonDynamoDB": "Databases",
    "AmazonS3": "Storage",
    "AWSDataTransfer": "Networking",
    "AmazonCloudWatch": "Management and Governance",
    "AWSLambda": "Compute",
    "AmazonECS": "Compute",
    "AmazonEKS": "Compute",
    "AmazonBedrock": "AI and Machine Learning",
    "AmazonSageMaker": "AI and Machine Learning",
    "AmazonComprehend": "AI and Machine Learning",
    "AWSCostExplorer": "Management and Governance",
    "AmazonVPC": "Networking",
    "AmazonRoute53": "Networking",
    "AmazonCloudFront": "Networking",
    "AWSCertificateManager": "Security",
    "AWSSecretsManager": "Security",
    "AWSKMS": "Security",
}

# CUR LineItemType -> FOCUS ChargeCategory
AWS_LINE_ITEM_TYPE_TO_FOCUS = {
    "Usage": "Usage",
    "Tax": "Tax",
    "Fee": "Purchase",
    "Refund": "Credit",
    "Credit": "Credit",
    "DiscountedUsage": "Usage",
    "SavingsPlanCoveredUsage": "Usage",
    "SavingsPlanNegation": "Adjustment",
    "SavingsPlanRecurringFee": "Purchase",
    "RIFee": "Purchase",
}


@dataclasses.dataclass
class MappedRow:
    focus_row: dict[str, object]
    warnings: list[str]
    fatal: bool = False


def _parse_time_interval(interval: str) -> tuple[dt.datetime | None, dt.datetime | None]:
    """CUR identity/TimeInterval is 'ISO_start/ISO_end'."""
    if "/" not in interval:
        return None, None
    s, e = interval.split("/", 1)
    try:
        start = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        end = dt.datetime.fromisoformat(e.replace("Z", "+00:00"))
        return start, end
    except (ValueError, AttributeError):
        return None, None


_BEDROCK_MODEL_RE = re.compile(r"foundation-model/([^/]+)$")


def _bedrock_model_from_resource_id(rid: str) -> str | None:
    """Extract the model id from a Bedrock resource ARN. Used for
    Bedrock per-model reporting (SPEC requirement #1).
    """
    m = _BEDROCK_MODEL_RE.search(rid or "")
    return m.group(1) if m else None


def map_row(cur: dict[str, str]) -> MappedRow:
    warnings: list[str] = []

    # --- ServiceCategory ---
    product_code = cur.get("lineItem/ProductCode", "").strip()
    if not product_code:
        warnings.append("lineItem/ProductCode blank --- ServiceCategory defaulted to 'Other'")
        focus_category = "Other"
    elif product_code in AWS_PRODUCT_TO_FOCUS_CATEGORY:
        focus_category = AWS_PRODUCT_TO_FOCUS_CATEGORY[product_code]
    else:
        warnings.append(
            f"ProductCode {product_code!r} not in mapping table --- defaulted to 'Other'. "
            "Add it to AWS_PRODUCT_TO_FOCUS_CATEGORY if this is a real product."
        )
        focus_category = "Other"

    # --- Cost / quantity ---
    try:
        unblended = float(cur.get("lineItem/UnblendedCost", "") or 0)
    except ValueError:
        warnings.append("lineItem/UnblendedCost unparseable; coerced to 0.0")
        unblended = 0.0
    try:
        usage_amount = float(cur.get("lineItem/UsageAmount", "") or 0)
    except ValueError:
        usage_amount = 0.0

    # --- Time ---
    interval = cur.get("identity/TimeInterval", "")
    start, end = _parse_time_interval(interval)

    # --- ChargeCategory ---
    line_item_type = cur.get("lineItem/LineItemType", "Usage")
    charge_category = AWS_LINE_ITEM_TYPE_TO_FOCUS.get(line_item_type, "Usage")
    if line_item_type and line_item_type not in AWS_LINE_ITEM_TYPE_TO_FOCUS:
        warnings.append(f"LineItemType {line_item_type!r} not mapped --- defaulted to 'Usage'")

    # --- Tags ---
    tags = {}
    for col, val in cur.items():
        if col.startswith("resourceTags/user:") and val:
            tag_key = col[len("resourceTags/user:"):]
            tags[tag_key] = val

    resource_id = cur.get("lineItem/ResourceId", "") or ""
    sku_meter = cur.get("lineItem/UsageType", "")

    # Bedrock per-model enrichment: surface the model id in SkuMeter so
    # downstream queries can group by model (SPEC s1 #1)
    bedrock_model = _bedrock_model_from_resource_id(resource_id)
    if bedrock_model and product_code == "AmazonBedrock":
        sku_meter = f"{bedrock_model}::{cur.get('lineItem/UsageType','')}"

    focus_row: dict[str, object] = {
        "BillingAccountId": cur.get("bill/PayerAccountId", ""),
        "BillingAccountName": "",   # AWS CUR doesn't carry a name field
        "SubAccountId": cur.get("lineItem/UsageAccountId", ""),
        "SubAccountName": "",
        "BillingPeriodStart": cur.get("bill/BillingPeriodStartDate", ""),
        "BillingPeriodEnd": cur.get("bill/BillingPeriodEndDate", ""),
        "ChargePeriodStart": start.isoformat() if start else "",
        "ChargePeriodEnd": end.isoformat() if end else "",
        "ChargeCategory": charge_category,
        "ChargeDescription": cur.get("lineItem/UsageType", ""),
        "ChargeFrequency": "Usage-Based",
        "BilledCost": unblended,
        "EffectiveCost": unblended,
        "ListCost": float(cur.get("pricing/publicOnDemandCost", "") or unblended),
        "ContractedCost": unblended,
        # AWS CUR generator emits USD; FOCUS BillingCurrency depends on the
        # account's preferences. For the PoC AnyBank account the home currency
        # is AED, so we don't carry USD through as BillingCurrency.
        # SPEC s3.1: the messiness here is that the source row is in USD
        # but the bank-side BillingCurrency is AED --- conversion happens
        # in a later pass (slice 3).
        "BillingCurrency": cur.get("lineItem/CurrencyCode", "USD"),
        "PricingCurrency": cur.get("lineItem/CurrencyCode", "USD"),
        "ServiceProviderName": "AWS",
        "InvoiceIssuerName": "AWS",
        "ServiceCategory": focus_category,
        "ServiceSubcategory": "",
        "ServiceName": cur.get("product/ProductName", "") or product_code,
        "SkuId": cur.get("lineItem/UsageType", ""),
        "SkuMeter": sku_meter,
        "SkuPriceId": "",
        "ResourceId": resource_id,
        "ResourceName": resource_id.split("/")[-1] if resource_id else "",
        "ResourceType": cur.get("product/instanceType", ""),
        "RegionId": cur.get("product/region", ""),
        "RegionName": cur.get("product/region", ""),
        "AvailabilityZone": cur.get("lineItem/AvailabilityZone", ""),
        "ConsumedQuantity": usage_amount,
        "ConsumedUnit": cur.get("pricing/unit", ""),
        "PricingQuantity": usage_amount,
        "PricingUnit": cur.get("pricing/unit", ""),
        "Tags": json.dumps(tags, separators=(",", ":")),
    }

    fatal = False
    if focus_row["ServiceCategory"] not in focus_spec.SERVICE_CATEGORIES_V1_3:
        warnings.append("ServiceCategory failed final conformance check")
        fatal = True
    if not focus_row["BillingCurrency"]:
        warnings.append("BillingCurrency is empty --- FOCUS requires this")
        fatal = True

    return MappedRow(focus_row=focus_row, warnings=warnings, fatal=fatal)


def normalize_csv(input_csv_path: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    focus_rows: list[dict[str, object]] = []
    report: list[dict[str, object]] = []
    seen_line_ids: set[str] = set()
    with open(input_csv_path) as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            line_id = row.get("identity/LineItemId", "")
            # Duplicate detection (SPEC s3.1 generator inserts a dup)
            if line_id and line_id in seen_line_ids:
                report.append({
                    "row_index": idx,
                    "fatal": False,
                    "warnings": [f"duplicate LineItemId {line_id} --- dropped on second sighting"],
                    "source_resource_id": row.get("lineItem/ResourceId", "")[:80],
                    "source_product_code": row.get("lineItem/ProductCode", ""),
                })
                continue
            seen_line_ids.add(line_id) if line_id else None

            mapped = map_row(row)
            report.append({
                "row_index": idx,
                "fatal": mapped.fatal,
                "warnings": mapped.warnings,
                "source_resource_id": row.get("lineItem/ResourceId", "")[:80],
                "source_product_code": row.get("lineItem/ProductCode", ""),
            })
            if not mapped.fatal:
                focus_rows.append(mapped.focus_row)
    return focus_rows, report


if __name__ == "__main__":
    in_path = sys.argv[1] if len(sys.argv) > 1 else "out/generators/aws_cur.csv"
    rows, report = normalize_csv(in_path)
    print(f"input:           {in_path}")
    print(f"rows mapped:     {len(rows)}")
    print(f"rows dropped:    {sum(1 for r in report if r['fatal'])}")
    print(f"duplicates dropped: {sum(1 for r in report if any('duplicate' in w for w in r['warnings']))}")
    print(f"rows with warns: {sum(1 for r in report if r['warnings'] and not r['fatal'])}")
    print()
    print("First 5 warning rows:")
    shown = 0
    for r in report:
        if r["warnings"]:
            print(f"  row {r['row_index']} [{r['source_product_code']}]: {r['warnings']}")
            shown += 1
            if shown >= 5:
                break
    print()
    print("Distinct ServiceCategory:", sorted({r["ServiceCategory"] for r in rows}))
    print("Bedrock rows:", sum(1 for r in rows if r["ServiceCategory"] == "AI and Machine Learning"))
