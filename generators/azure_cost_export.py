"""Generate a synthetic Azure cost-export-shaped CSV.

Azure's exported cost file (the "Daily" or "Actual Cost" export) uses
**PascalCase** columns, **MeterCategory / MeterSubCategory** instead of a
single ServiceName, and **separate billing vs pricing currency columns** ---
all different from AWS CUR. The Azure->FOCUS mapping is therefore SPEC s2's
leading-edge concern.

Messiness injected per SPEC s3.1:
  - Some rows have **`ServiceFamily`-style category names that look
    plausible but are NOT FOCUS ServiceCategory allowed values** ("Compute",
    "AI + Machine Learning"). The normalizer must map them to the closed
    set --- GOTCHA F-2.
  - **ARM-path ResourceId** for join (GOTCHA J-1 --- Azure joins on ARM path,
    AWS/OCI join on instance ID).
  - **Tag column is a single JSON string column**, not multiple columns ---
    a real Azure quirk that breaks naive CSV-tag-extraction code.
  - **Mixed BillingCurrency=AED, PricingCurrency=USD** with both
    `CostInBillingCurrency` and `CostInPricingCurrency` populated.

Output file: out/generators/azure_cost.csv
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators import common

# Authentic Azure cost-export columns. Trimmed but representative.
AZURE_COLUMNS = [
    "InvoiceSectionName",
    "AccountName",
    "AccountOwnerId",
    "SubscriptionId",
    "SubscriptionName",
    "ResourceGroup",
    "ResourceLocation",
    "Date",
    "ProductName",
    "MeterCategory",
    "MeterSubCategory",
    "MeterId",
    "MeterName",
    "MeterRegion",
    "UnitOfMeasure",
    "Quantity",
    "EffectivePrice",
    "CostInBillingCurrency",
    "CostInPricingCurrency",
    "BillingCurrency",
    "PricingCurrency",
    "ResourceId",
    "ServiceFamily",
    "Tags",                # JSON-string-in-a-cell --- real Azure quirk
    "ChargeType",          # Usage / Purchase / Refund / Adjustment
    "PublisherType",
    "BillingPeriodStartDate",
    "BillingPeriodEndDate",
]


def _row(
    *,
    date: dt.date,
    rg: str,
    product_name: str,
    meter_category: str,
    meter_subcategory: str,
    meter_name: str,
    service_family: str,
    region: str,
    resource_id: str,
    quantity: float,
    cost_usd: float,
    unit: str,
    tags_dict: dict[str, str] | None = None,
    charge_type: str = "Usage",
) -> dict[str, str]:
    cost_aed = common.usd_to_aed(cost_usd)
    bp_start = dt.date(date.year, date.month, 1)
    nm = bp_start.month % 12 + 1
    ny = bp_start.year + (1 if bp_start.month == 12 else 0)
    bp_end = dt.date(ny, nm, 1) - dt.timedelta(days=1)
    return {
        "InvoiceSectionName": "DEMO-ENBD-IT",
        "AccountName": "DEMO Enrollment",
        "AccountOwnerId": "demo.owner@example.invalid",
        "SubscriptionId": common.FAKE_AZURE_SUBSCRIPTION,
        "SubscriptionName": "DEMO-ENBD-Sub-Prod",
        "ResourceGroup": rg,
        "ResourceLocation": region,
        "Date": date.isoformat(),
        "ProductName": product_name,
        "MeterCategory": meter_category,
        "MeterSubCategory": meter_subcategory,
        "MeterId": "meter-demo-0000",
        "MeterName": meter_name,
        "MeterRegion": region,
        "UnitOfMeasure": unit,
        "Quantity": f"{quantity:.6f}",
        "EffectivePrice": f"{(cost_usd / quantity) if quantity else 0:.6f}",
        # NB: Azure exports BOTH billing- and pricing-currency cost on every
        # row, with the FX rate implicit. This is the column duplication
        # that maps to FOCUS BilledCost vs EffectiveCost on the pricing-
        # currency side.
        "CostInBillingCurrency": f"{cost_aed:.6f}",
        "CostInPricingCurrency": f"{cost_usd:.6f}",
        "BillingCurrency": common.BILLING_CURRENCY,
        "PricingCurrency": common.PRICING_CURRENCY,
        "ResourceId": resource_id,
        "ServiceFamily": service_family,
        # The Tags column is a single JSON-encoded object STRING in Azure
        # exports --- escaping/quoting it through CSV is the gotcha.
        "Tags": json.dumps(tags_dict or {}, separators=(",", ":")),
        "ChargeType": charge_type,
        "PublisherType": "Azure",
        "BillingPeriodStartDate": bp_start.isoformat(),
        "BillingPeriodEndDate": bp_end.isoformat(),
    }


def generate(days: int = 3) -> list[dict[str, str]]:
    rng = common.make_rng()
    rows: list[dict[str, str]] = []
    azure_workloads = [w for w in common.WORKLOADS if w.azure_resource_id]
    start_date = dt.date(2026, 6, 1)

    # Daily VM rows for each Azure workload
    for d_offset in range(days):
        day = start_date + dt.timedelta(days=d_offset)
        for wl in azure_workloads:
            # Pull the RG out of the ARM path so the row's RG column matches
            arm = wl.azure_resource_id or ""
            rg = "rg-unknown"
            try:
                rg = arm.split("/resourceGroups/")[1].split("/")[0]
            except Exception:
                pass
            base_daily = 1.2 * wl.cpu_cores + 0.04 * (wl.memory_mb / 1024)
            cost = base_daily + rng.uniform(-0.05, 0.05)
            rows.append(
                _row(
                    date=day,
                    rg=rg,
                    product_name="Virtual Machines D-Series Demo",
                    meter_category="Virtual Machines",
                    meter_subcategory="D Series",
                    meter_name=f"D{wl.cpu_cores}s v3 me-vm",
                    # NB: 'Compute' is Azure's ServiceFamily value --- it is
                    # NOT a FOCUS ServiceCategory allowed value. Normalizer
                    # must map 'Compute' -> 'Compute' (which happens to be
                    # valid in FOCUS), and similarly for the AI rows below.
                    service_family="Compute",
                    region=common.AZURE_REGIONS[0],
                    resource_id=arm,
                    quantity=24.0,  # one day = 24 hours
                    cost_usd=max(0.01, cost),
                    unit="1 Hour",
                    tags_dict=wl.tags,
                )
            )

    # An Azure OpenAI row --- meter category looks AI-shaped but exact label
    # ("AI + Machine Learning") needs explicit mapping to the FOCUS
    # 'AI and Machine Learning' value (note the '&' vs 'and' difference).
    aoai_day = start_date + dt.timedelta(days=1)
    rows.append(
        _row(
            date=aoai_day,
            rg="rg-ai-prod",
            product_name="Azure OpenAI Service",
            meter_category="AI + Machine Learning",  # <-- not FOCUS string
            meter_subcategory="GPT-4 Turbo Tokens",
            meter_name="Input Tokens",
            service_family="AI + Machine Learning",
            region=common.AZURE_REGIONS[0],
            resource_id=(
                f"/subscriptions/{common.FAKE_AZURE_SUBSCRIPTION}"
                "/resourceGroups/rg-ai-prod"
                "/providers/Microsoft.CognitiveServices/accounts/demo-aoai"
            ),
            quantity=125_000.0,
            cost_usd=0.85,
            unit="1K Tokens",
            tags_dict={"app": "ai-assist", "env": "prod"},
        )
    )

    # --- Messiness injections ---

    # 1) A "Refund" charge type, negative-cost row --- the normalizer must
    #    preserve sign and map ChargeType to FOCUS ChargeCategory.
    rows.append(
        _row(
            date=start_date,
            rg="rg-billing-adj",
            product_name="Adjustment",
            meter_category="Adjustment",
            meter_subcategory="Credit",
            meter_name="Promotional Credit",
            service_family="Other",
            region=common.AZURE_REGIONS[0],
            resource_id="",
            quantity=1.0,
            cost_usd=-12.0,
            unit="1 Each",
            tags_dict={},
            charge_type="Refund",
        )
    )

    # 2) Tags column with a quote/comma in a value --- exercises CSV-quoting
    #    in the loader. Real Azure tags can contain commas.
    if azure_workloads:
        wl = azure_workloads[0]
        rows.append(
            _row(
                date=start_date + dt.timedelta(days=2),
                rg="rg-risk-prod",
                product_name="Virtual Machines D-Series Demo",
                meter_category="Virtual Machines",
                meter_subcategory="D Series",
                meter_name=f"D{wl.cpu_cores}s v3 me-vm",
                service_family="Compute",
                region=common.AZURE_REGIONS[0],
                resource_id=wl.azure_resource_id or "",
                quantity=24.0,
                cost_usd=5.0,
                unit="1 Hour",
                tags_dict={
                    "app": "fraud-det",
                    "notes": 'pilot, owner="Alice Bee"',  # comma + quote
                    "env": "prod",
                },
            )
        )

    return rows


def write_csv(path: str | None = None) -> str:
    rows = generate(days=3)
    out = path or os.path.join(common.out_dir(), "azure_cost.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="") as f:
        # quoting=ALL is closer to real Azure exports (every cell quoted)
        w = csv.DictWriter(f, fieldnames=AZURE_COLUMNS, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return out


if __name__ == "__main__":
    p = write_csv()
    print(f"wrote {p}")
