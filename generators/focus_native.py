"""Generate synthetic data in the EXACT native FOCUS 1.2 export shape.

Unlike aws_cur / azure_cost_export / oci_usage (which emit provider-NATIVE
billing formats — the pre-FOCUS-native / historical path), this generator
emits what AWS / Azure / OCI consoles produce TODAY: a native FOCUS export.

Verified ground truth (focus.finops.org provider registry + AWS data
dictionary, 2026-06-26 — see GOTCHAS NF-1):
  - AWS  "FOCUS 1.2 with AWS columns" = FOCUS columns + x_Discounts,
         x_Operation, x_ServiceCode (SQL table FOCUS_1_2_AWS).
  - Azure FOCUS 1.2 export via Cost Management.
  - OCI   FOCUS 1.0 export (version skew — leveled by the adapter).

Provider extension columns use the FOCUS `x_` convention. The join
identifiers (GOTCHAS J-1) are preserved in ResourceId exactly as each
provider emits them: AWS = instance id, Azure = ARM path, OCI = OCID —
so the FOCUS<->ManageIQ join logic is unchanged.

Output: out/generators/focus_<provider>.csv
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators import common
from normalizer import focus_spec

# Native FOCUS export = the FOCUS column set + provider x_ extensions.
AWS_X_COLUMNS = ["x_Discounts", "x_Operation", "x_ServiceCode"]
AZURE_X_COLUMNS = ["x_SkuMeterId", "x_ResourceGroupName"]
OCI_X_COLUMNS = ["x_CompartmentId"]


def _base_row() -> dict[str, object]:
    """A FOCUS row with every v1.3 column present (blank by default)."""
    return {col: "" for col in focus_spec.FOCUS_COLUMNS_V1_3}


def _period(day: dt.date) -> tuple[str, str, str, str]:
    bp_start = dt.date(day.year, day.month, 1)
    nm = bp_start.month % 12 + 1
    ny = bp_start.year + (1 if bp_start.month == 12 else 0)
    bp_end = dt.date(ny, nm, 1)
    cp_start = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
    cp_end = cp_start + dt.timedelta(days=1)
    return bp_start.isoformat(), bp_end.isoformat(), cp_start.isoformat(), cp_end.isoformat()


# ----------------------------------------------------------------------
# AWS native FOCUS 1.2
# ----------------------------------------------------------------------
def generate_aws(days: int = 3) -> tuple[list[dict], list[str]]:
    rng = common.make_rng()
    rows: list[dict] = []
    cols = focus_spec.FOCUS_COLUMNS_V1_3 + AWS_X_COLUMNS
    start = dt.date(2026, 6, 1)
    aws_wls = [w for w in common.WORKLOADS if w.aws_instance_id]

    for d in range(days):
        day = start + dt.timedelta(days=d)
        bps, bpe, cps, cpe = _period(day)
        for wl in aws_wls:
            r = _base_row()
            cost = round(24 * (0.05 * wl.cpu_cores) + rng.uniform(-0.1, 0.1), 6)
            r.update({
                "BillingAccountId": common.FAKE_AWS_ACCOUNT_ID,
                "BillingAccountName": "DEMO-ENBD-AWS",
                "SubAccountId": common.FAKE_AWS_ACCOUNT_ID,
                "BillingPeriodStart": bps, "BillingPeriodEnd": bpe,
                "ChargePeriodStart": cps, "ChargePeriodEnd": cpe,
                "ChargeCategory": "Usage", "ChargeClass": "",
                "ChargeDescription": f"EC2 demo.{wl.cpu_cores}xlarge",
                "BilledCost": cost, "EffectiveCost": cost, "ListCost": cost, "ContractedCost": cost,
                "BillingCurrency": "USD", "PricingCurrency": "USD",
                "ServiceProviderName": "AWS", "InvoiceIssuerName": "Amazon Web Services, Inc.",
                "ServiceCategory": "Compute", "ServiceName": "Amazon Elastic Compute Cloud",
                "SkuId": f"demo.{wl.cpu_cores}xlarge", "SkuMeter": "BoxUsage",
                "ResourceId": wl.aws_instance_id,          # J-1: AWS joins on instance id
                "ResourceName": wl.name_in_provider("aws"),
                "ResourceType": "Instance",
                "RegionId": "me-central-1", "RegionName": "Middle East (UAE)",
                "ConsumedQuantity": 24, "ConsumedUnit": "Hrs",
                "PricingQuantity": 24, "PricingUnit": "Hrs",
                "Tags": json.dumps(wl.tags, separators=(",", ":")),
                "x_Discounts": "0", "x_Operation": "RunInstances", "x_ServiceCode": "AmazonEC2",
            })
            rows.append(r)

    # Bedrock per-model AI rows (requirement #1) — native FOCUS shape.
    bd = dt.date(2026, 6, 1)
    bps, bpe, cps, cpe = _period(bd)
    for model_id, in_p, out_p in common.BEDROCK_MODELS:
        in_tok = rng.randint(50_000, 200_000)
        out_tok = rng.randint(10_000, 50_000)
        for kind, tok, price in (("Input", in_tok, in_p), ("Output", out_tok, out_p)):
            r = _base_row()
            cost = round((tok / 1000.0) * price, 6)
            r.update({
                "BillingAccountId": common.FAKE_AWS_ACCOUNT_ID,
                "BillingPeriodStart": bps, "BillingPeriodEnd": bpe,
                "ChargePeriodStart": cps, "ChargePeriodEnd": cpe,
                "ChargeCategory": "Usage",
                "ChargeDescription": f"Bedrock {model_id} {kind} tokens",
                "BilledCost": cost, "EffectiveCost": cost, "ListCost": cost, "ContractedCost": cost,
                "BillingCurrency": "USD", "PricingCurrency": "USD",
                "ServiceProviderName": "AWS", "InvoiceIssuerName": "Amazon Web Services, Inc.",
                "ServiceCategory": "AI and Machine Learning", "ServiceName": "Amazon Bedrock",
                "SkuMeter": f"{model_id}::{kind}Tokens",   # model id preserved for the AI view
                "SkuId": f"{model_id}-{kind.lower()}",
                "ResourceId": f"arn:aws:bedrock:us-east-1::foundation-model/{model_id}",
                "ResourceName": model_id, "ResourceType": "Foundation Model",
                "RegionId": "us-east-1", "RegionName": "US East (N. Virginia)",
                "ConsumedQuantity": tok, "ConsumedUnit": "Tokens",
                "PricingQuantity": tok, "PricingUnit": "Tokens",
                "Tags": json.dumps({"app": "ai-assist", "env": "prod"}, separators=(",", ":")),
                "x_Discounts": "0", "x_Operation": "InvokeModel", "x_ServiceCode": "AmazonBedrock",
            })
            rows.append(r)

    # Messiness: a duplicate row + a null-ServiceCategory row (the normalizer/
    # adapter must catch the latter — FOCUS mandates ServiceCategory non-null).
    if rows:
        rows.append(dict(rows[0]))                          # duplicate
    bad = _base_row()
    bad.update({
        "BillingAccountId": common.FAKE_AWS_ACCOUNT_ID,
        "ChargePeriodStart": cps, "ChargePeriodEnd": cpe,
        "BilledCost": 0.42, "BillingCurrency": "USD",
        "ServiceProviderName": "AWS", "ServiceCategory": "",   # <-- null, on purpose
        "ResourceId": "arn:aws:demo:???", "ChargeDescription": "mystery charge",
        "x_ServiceCode": "UNKNOWN",
    })
    rows.append(bad)
    return rows, cols


# ----------------------------------------------------------------------
# Azure native FOCUS 1.2
# ----------------------------------------------------------------------
def generate_azure(days: int = 3) -> tuple[list[dict], list[str]]:
    rng = common.make_rng()
    rows: list[dict] = []
    cols = focus_spec.FOCUS_COLUMNS_V1_3 + AZURE_X_COLUMNS
    start = dt.date(2026, 6, 1)
    az_wls = [w for w in common.WORKLOADS if w.azure_resource_id]

    for d in range(days):
        day = start + dt.timedelta(days=d)
        bps, bpe, cps, cpe = _period(day)
        for wl in az_wls:
            arm = wl.azure_resource_id or ""
            rg = arm.split("/resourceGroups/")[1].split("/")[0] if "/resourceGroups/" in arm else ""
            r = _base_row()
            cost_usd = round(24 * (1.2 * wl.cpu_cores / 24) + rng.uniform(-0.1, 0.1), 6)
            r.update({
                "BillingAccountId": common.FAKE_AZURE_SUBSCRIPTION,
                "BillingAccountName": "DEMO-ENBD-Azure",
                "SubAccountId": rg, "SubAccountName": rg,
                "BillingPeriodStart": bps, "BillingPeriodEnd": bpe,
                "ChargePeriodStart": cps, "ChargePeriodEnd": cpe,
                "ChargeCategory": "Usage",
                "ChargeDescription": f"Virtual Machines D{wl.cpu_cores}s v3",
                # Azure reports billing (AED) + pricing (USD) — mixed currency.
                "BilledCost": common.usd_to_aed(cost_usd), "EffectiveCost": common.usd_to_aed(cost_usd),
                "ListCost": cost_usd, "ContractedCost": common.usd_to_aed(cost_usd),
                "BillingCurrency": "AED", "PricingCurrency": "USD",
                "ServiceProviderName": "Microsoft", "InvoiceIssuerName": "Microsoft",
                "ServiceCategory": "Compute", "ServiceName": "Virtual Machines",
                "SkuMeter": f"D{wl.cpu_cores}s v3",
                "ResourceId": arm,                          # J-1: Azure joins on ARM path
                "ResourceName": arm.split("/")[-1],
                "ResourceType": "Microsoft.Compute/virtualMachines",
                "RegionId": "uaenorth", "RegionName": "UAE North",
                "ConsumedQuantity": 24, "ConsumedUnit": "Hour",
                "PricingQuantity": 24, "PricingUnit": "Hour",
                "Tags": json.dumps(wl.tags, separators=(",", ":")),
                "x_SkuMeterId": "demo-meter-0000", "x_ResourceGroupName": rg,
            })
            rows.append(r)

    # Azure OpenAI AI row
    bps, bpe, cps, cpe = _period(dt.date(2026, 6, 2))
    r = _base_row()
    r.update({
        "BillingAccountId": common.FAKE_AZURE_SUBSCRIPTION,
        "SubAccountId": "rg-ai-prod",
        "BillingPeriodStart": bps, "BillingPeriodEnd": bpe,
        "ChargePeriodStart": cps, "ChargePeriodEnd": cpe,
        "ChargeCategory": "Usage", "ChargeDescription": "Azure OpenAI GPT-4 Turbo input tokens",
        "BilledCost": common.usd_to_aed(0.85), "EffectiveCost": common.usd_to_aed(0.85),
        "ListCost": 0.85, "ContractedCost": common.usd_to_aed(0.85),
        "BillingCurrency": "AED", "PricingCurrency": "USD",
        "ServiceProviderName": "Microsoft", "InvoiceIssuerName": "Microsoft",
        "ServiceCategory": "AI and Machine Learning", "ServiceName": "Azure OpenAI Service",
        "SkuMeter": "gpt-4-turbo::InputTokens",
        "ResourceId": f"/subscriptions/{common.FAKE_AZURE_SUBSCRIPTION}/resourceGroups/rg-ai-prod/providers/Microsoft.CognitiveServices/accounts/demo-aoai",
        "ResourceName": "demo-aoai", "ResourceType": "Microsoft.CognitiveServices/accounts",
        "RegionId": "uaenorth", "RegionName": "UAE North",
        "ConsumedQuantity": 125000, "ConsumedUnit": "Tokens",
        "PricingQuantity": 125000, "PricingUnit": "1K Tokens",
        "Tags": json.dumps({"app": "ai-assist", "env": "prod"}, separators=(",", ":")),
        "x_SkuMeterId": "demo-aoai-meter", "x_ResourceGroupName": "rg-ai-prod",
    })
    rows.append(r)
    return rows, cols


# ----------------------------------------------------------------------
# OCI native FOCUS 1.0  (version skew — adapter levels to 1.2 target)
# ----------------------------------------------------------------------
def generate_oci(days: int = 3) -> tuple[list[dict], list[str]]:
    rng = common.make_rng()
    rows: list[dict] = []
    cols = focus_spec.FOCUS_COLUMNS_V1_3 + OCI_X_COLUMNS
    start = dt.date(2026, 6, 1)
    oci_wls = [w for w in common.WORKLOADS if w.oci_resource_id]

    for d in range(days):
        day = start + dt.timedelta(days=d)
        bps, bpe, cps, cpe = _period(day)
        for wl in oci_wls:
            r = _base_row()
            cost = round(24 * (0.04 * wl.cpu_cores) + rng.uniform(-0.05, 0.05), 6)
            r.update({
                "BillingAccountId": common.FAKE_OCI_TENANCY,
                "BillingAccountName": "DEMO-OCI-Tenancy",
                "SubAccountId": "Analytics", "SubAccountName": "Analytics",
                "BillingPeriodStart": bps, "BillingPeriodEnd": bpe,
                "ChargePeriodStart": cps, "ChargePeriodEnd": cpe,
                "ChargeCategory": "Usage", "ChargeDescription": "Compute VM.Standard",
                "BilledCost": cost, "EffectiveCost": cost, "ListCost": cost, "ContractedCost": cost,
                "BillingCurrency": "USD", "PricingCurrency": "USD",
                "ServiceProviderName": "Oracle Cloud Infrastructure", "InvoiceIssuerName": "Oracle",
                "ServiceCategory": "Compute", "ServiceName": "Compute",
                "SkuId": "B91449", "SkuMeter": "VM.Standard",
                "ResourceId": wl.oci_resource_id,           # J-1: OCI joins on OCID
                "ResourceName": wl.name_in_provider("oci"), "ResourceType": "Instance",
                "RegionId": "me-dubai-1", "RegionName": "UAE Central (Dubai)",
                "ConsumedQuantity": 24, "ConsumedUnit": "Hours",
                "PricingQuantity": 24, "PricingUnit": "Hours",
                "Tags": json.dumps(wl.tags, separators=(",", ":")),
                "x_CompartmentId": "ocid1.compartment.oc1..demoanalytics",
            })
            rows.append(r)

    # OCI generative-AI rows (per-model)
    bps, bpe, cps, cpe = _period(dt.date(2026, 6, 1))
    for model_id, sku, price_10k, tokens in (
        ("cohere.command-r-plus", "B99001", 0.0150, 180_000),
        ("cohere.command-r-08-2024", "B99002", 0.0030, 120_000),
        ("meta.llama-3.1-70b-instruct", "B99003", 0.0072, 90_000),
    ):
        r = _base_row()
        cost = round((tokens / 10_000.0) * price_10k, 6)
        r.update({
            "BillingAccountId": common.FAKE_OCI_TENANCY, "SubAccountId": "Analytics",
            "BillingPeriodStart": bps, "BillingPeriodEnd": bpe,
            "ChargePeriodStart": cps, "ChargePeriodEnd": cpe,
            "ChargeCategory": "Usage", "ChargeDescription": f"OCI Generative AI {model_id}",
            "BilledCost": cost, "EffectiveCost": cost, "ListCost": cost, "ContractedCost": cost,
            "BillingCurrency": "USD", "PricingCurrency": "USD",
            "ServiceProviderName": "Oracle Cloud Infrastructure", "InvoiceIssuerName": "Oracle",
            "ServiceCategory": "AI and Machine Learning", "ServiceName": "Generative AI",
            "SkuId": sku, "SkuMeter": model_id,
            "ResourceId": f"ocid1.generativeaimodel.oc1.me-dubai-1.demo{sku.lower()}",
            "ResourceName": model_id, "ResourceType": "Generative AI Model",
            "RegionId": "me-dubai-1", "RegionName": "UAE Central (Dubai)",
            "ConsumedQuantity": tokens, "ConsumedUnit": "Tokens",
            "PricingQuantity": tokens, "PricingUnit": "Tokens",
            "Tags": json.dumps({"app": "ai-assist", "env": "prod"}, separators=(",", ":")),
            "x_CompartmentId": "ocid1.compartment.oc1..demoanalytics",
        })
        rows.append(r)
    return rows, cols


def _write(path: str, rows: list[dict], cols: list[str]) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def write_all() -> dict[str, str]:
    out = common.out_dir()
    paths = {}
    for name, gen in (("aws", generate_aws), ("azure", generate_azure), ("oci", generate_oci)):
        rows, cols = gen()
        paths[name] = _write(os.path.join(out, f"focus_{name}.csv"), rows, cols)
    return paths


if __name__ == "__main__":
    for name, path in write_all().items():
        import csv as _csv
        n = sum(1 for _ in open(path)) - 1
        print(f"{name:6s} -> {path}  ({n} rows)")
