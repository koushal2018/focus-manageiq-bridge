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


def _non_usage_rows(rng, account_id, account_name, bps, bpe, cps, cpe,
                    provider_name, issuer, currency, x_extra):
    """The charge categories real exports carry beyond Usage: a monthly Tax
    line, a commitment Purchase, a Credit, and a Refund. Small, fixed set per
    day — scaled by the caller. `x_extra` is the provider x_ dict to merge."""
    out = []
    specs = [
        ("Tax",      "Management and Governance", "VAT on cloud services", 12.50),
        ("Purchase", "Compute",                   "Compute Savings Plan (1yr, no upfront)", 240.00),
        ("Credit",   "Other",                     "Promotional credit", -35.00),
        ("Refund",   "Compute",                   "Refund — overcharge correction", -8.75),
    ]
    for cat, svc_cat, desc, amount in specs:
        r = _base_row()
        r.update({
            "BillingAccountId": account_id, "BillingAccountName": account_name,
            "BillingPeriodStart": bps, "BillingPeriodEnd": bpe,
            "ChargePeriodStart": cps, "ChargePeriodEnd": cpe,
            "ChargeCategory": cat, "ChargeDescription": desc,
            "BilledCost": amount, "EffectiveCost": amount,
            "ListCost": amount, "ContractedCost": amount,
            "BillingCurrency": currency, "PricingCurrency": "USD",
            "ServiceProviderName": provider_name, "InvoiceIssuerName": issuer,
            "ServiceCategory": svc_cat, "ServiceName": "Account-level charge",
            "ChargeFrequency": "One-Time" if cat in ("Credit", "Refund") else "Recurring",
        })
        r.update(x_extra)
        out.append(r)
    return out


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
            for _ in range(common.gen_scale()):
                sub = rng.choice(common.SUB_ACCOUNTS["aws"])
                # vCPU = the instance size (demo.Nxlarge -> N vCPU). Priced via the
                # real per-vCPU-hr rate model so ListUnitPrice is comparable (FIN-2).
                pc = common.compute_charge(rng, "aws", vcpu=wl.cpu_cores, hours=24.0)
                r = _base_row()
                r.update({
                    "BillingAccountId": common.FAKE_AWS_ACCOUNT_ID,
                    "BillingAccountName": "DEMO-ENBD-AWS",
                    "SubAccountId": sub, "SubAccountName": sub,
                    "BillingPeriodStart": bps, "BillingPeriodEnd": bpe,
                    "ChargePeriodStart": cps, "ChargePeriodEnd": cpe,
                    "ChargeCategory": "Usage", "ChargeClass": "",
                    "ChargeDescription": f"EC2 demo.{wl.cpu_cores}xlarge",
                    "BilledCost": pc["BilledCost"], "EffectiveCost": pc["EffectiveCost"],
                    "ListCost": pc["ListCost"], "ContractedCost": pc["ContractedCost"],
                    "ListUnitPrice": pc["ListUnitPrice"],
                    "ContractedUnitPrice": pc["ContractedUnitPrice"],
                    "PricingCategory": pc["PricingCategory"],
                    "BillingCurrency": "USD", "PricingCurrency": "USD",
                    "ServiceProviderName": "AWS", "InvoiceIssuerName": "Amazon Web Services, Inc.",
                    "ServiceCategory": "Compute", "ServiceName": "Amazon Elastic Compute Cloud",
                    "SkuId": f"demo.{wl.cpu_cores}xlarge", "SkuMeter": "BoxUsage",
                    "SkuPriceId": f"aws-ec2-demo.{wl.cpu_cores}xlarge-od",
                    "ResourceId": wl.aws_instance_id,
                    "ResourceName": wl.name_in_provider("aws"),
                    "ResourceType": "Instance",
                    "RegionId": "me-central-1", "RegionName": "Middle East (UAE)",
                    "ConsumedQuantity": pc["ConsumedQuantity"], "ConsumedUnit": pc["ConsumedUnit"],
                    "PricingQuantity": pc["PricingQuantity"], "PricingUnit": pc["PricingUnit"],
                    "Tags": common.tag_sparsity(rng, wl.tags),
                    "CommitmentDiscountId": pc["CommitmentDiscountId"],
                    "CommitmentDiscountStatus": pc["CommitmentDiscountStatus"],
                    "x_Discounts": "0", "x_Operation": "RunInstances", "x_ServiceCode": "AmazonEC2",
                })
                rows.append(r)
        # account-level non-usage charges (Tax/Purchase/Credit/Refund)
        rows.extend(_non_usage_rows(
            rng, common.FAKE_AWS_ACCOUNT_ID, "DEMO-ENBD-AWS", bps, bpe, cps, cpe,
            "AWS", "Amazon Web Services, Inc.", "USD",
            {"x_Discounts": "0", "x_Operation": "", "x_ServiceCode": "AccountCharge"}))

    # Bedrock per-model AI rows (requirement #1) — native FOCUS shape.
    # Spread daily AI usage across the window so AI cost scales with `days`.
    for d in range(days):
        bd = dt.date(2026, 6, 1) + dt.timedelta(days=d)
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
            for _ in range(common.gen_scale()):
                sub = rng.choice(common.SUB_ACCOUNTS["azure"])
                arm = wl.azure_resource_id or ""
                rg = arm.split("/resourceGroups/")[1].split("/")[0] if "/resourceGroups/" in arm else ""
                r = _base_row()
                # Priced in USD by the rate model, then converted to AED so the
                # WHOLE row (costs AND unit prices) is in BillingCurrency=AED —
                # the B-6/B-7 currency-integrity rule applies to unit prices too.
                pc = common.compute_charge(rng, "azure", vcpu=wl.cpu_cores, hours=24.0)
                aed = common.usd_to_aed
                r.update({
                    "BillingAccountId": common.FAKE_AZURE_SUBSCRIPTION,
                    "BillingAccountName": "DEMO-ENBD-Azure",
                    "SubAccountId": sub, "SubAccountName": sub,
                    "BillingPeriodStart": bps, "BillingPeriodEnd": bpe,
                    "ChargePeriodStart": cps, "ChargePeriodEnd": cpe,
                    "ChargeCategory": "Usage",
                    "ChargeDescription": f"Virtual Machines D{wl.cpu_cores}s v3",
                    "BilledCost": aed(pc["BilledCost"]), "EffectiveCost": aed(pc["EffectiveCost"]),
                    "ListCost": aed(pc["ListCost"]), "ContractedCost": aed(pc["ContractedCost"]),
                    "ListUnitPrice": aed(pc["ListUnitPrice"]),
                    "ContractedUnitPrice": aed(pc["ContractedUnitPrice"]),
                    "PricingCategory": pc["PricingCategory"],
                    "BillingCurrency": "AED", "PricingCurrency": "AED",
                    "ServiceProviderName": "Microsoft", "InvoiceIssuerName": "Microsoft",
                    "ServiceCategory": "Compute", "ServiceName": "Virtual Machines",
                    "SkuMeter": f"D{wl.cpu_cores}s v3",
                    "SkuId": f"D{wl.cpu_cores}s_v3",
                    "SkuPriceId": f"azure-vm-D{wl.cpu_cores}s_v3-od",
                    "ResourceId": arm,
                    "ResourceName": arm.split("/")[-1],
                    "ResourceType": "Microsoft.Compute/virtualMachines",
                    "RegionId": "uaenorth", "RegionName": "UAE North",
                    "ConsumedQuantity": pc["ConsumedQuantity"], "ConsumedUnit": "Hour",
                    "PricingQuantity": pc["PricingQuantity"], "PricingUnit": "vCPU-Hours",
                    "Tags": common.tag_sparsity(rng, wl.tags),
                    "CommitmentDiscountId": pc["CommitmentDiscountId"],
                    "CommitmentDiscountStatus": pc["CommitmentDiscountStatus"],
                    "x_SkuMeterId": "demo-meter-0000", "x_ResourceGroupName": rg,
                })
                rows.append(r)
        # account-level non-usage charges (Tax/Purchase/Credit/Refund)
        rows.extend(_non_usage_rows(
            rng, common.FAKE_AZURE_SUBSCRIPTION, "DEMO-ENBD-Azure", bps, bpe, cps, cpe,
            "Microsoft", "Microsoft", "AED",
            {"x_SkuMeterId": "", "x_ResourceGroupName": ""}))

    # Azure OpenAI AI rows — spread across the window (parity with Bedrock).
    for d in range(days):
        cd = dt.date(2026, 6, 1) + dt.timedelta(days=d)
        bps, bpe, cps, cpe = _period(cd)
        for meter, usd in (("gpt-4-turbo::InputTokens", 0.85), ("gpt-4-turbo::OutputTokens", 1.10)):
            usd_j = round(usd + rng.uniform(-0.2, 0.4), 6)
            r = _base_row()
            r.update({
                "BillingAccountId": common.FAKE_AZURE_SUBSCRIPTION,
                "SubAccountId": "rg-ai-prod",
                "BillingPeriodStart": bps, "BillingPeriodEnd": bpe,
                "ChargePeriodStart": cps, "ChargePeriodEnd": cpe,
                "ChargeCategory": "Usage", "ChargeDescription": f"Azure OpenAI {meter}",
                "BilledCost": common.usd_to_aed(usd_j), "EffectiveCost": common.usd_to_aed(usd_j),
                "ListCost": common.usd_to_aed(usd_j), "ContractedCost": common.usd_to_aed(usd_j),
                "BillingCurrency": "AED", "PricingCurrency": "USD",
                "ServiceProviderName": "Microsoft", "InvoiceIssuerName": "Microsoft",
                "ServiceCategory": "AI and Machine Learning", "ServiceName": "Azure OpenAI Service",
                "SkuMeter": meter,
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
            for _ in range(common.gen_scale()):
                sub = rng.choice(common.SUB_ACCOUNTS["oci"])
                # OCI SKU now carries the OCPU count so it's size-comparable (the
                # old 'VM.Standard'/B91449 encoded no size — FIN-2). vCPU≈OCPU here.
                pc = common.compute_charge(rng, "oci", vcpu=wl.cpu_cores, hours=24.0)
                r = _base_row()
                r.update({
                    "BillingAccountId": common.FAKE_OCI_TENANCY,
                    "BillingAccountName": "DEMO-OCI-Tenancy",
                    "SubAccountId": sub, "SubAccountName": sub,
                    "BillingPeriodStart": bps, "BillingPeriodEnd": bpe,
                    "ChargePeriodStart": cps, "ChargePeriodEnd": cpe,
                    "ChargeCategory": "Usage",
                    "ChargeDescription": f"Compute VM.Standard.E4.Flex ({wl.cpu_cores} OCPU)",
                    "BilledCost": pc["BilledCost"], "EffectiveCost": pc["EffectiveCost"],
                    "ListCost": pc["ListCost"], "ContractedCost": pc["ContractedCost"],
                    "ListUnitPrice": pc["ListUnitPrice"],
                    "ContractedUnitPrice": pc["ContractedUnitPrice"],
                    "PricingCategory": pc["PricingCategory"],
                    "BillingCurrency": "USD", "PricingCurrency": "USD",
                    "ServiceProviderName": "Oracle Cloud Infrastructure", "InvoiceIssuerName": "Oracle",
                    "ServiceCategory": "Compute", "ServiceName": "Compute",
                    "SkuId": f"B91449-E4Flex-{wl.cpu_cores}ocpu", "SkuMeter": "VM.Standard.E4.Flex - OCPU",
                    "SkuPriceId": f"oci-compute-e4flex-{wl.cpu_cores}ocpu-od",
                    "ResourceId": wl.oci_resource_id,
                    "ResourceName": wl.name_in_provider("oci"), "ResourceType": "Instance",
                    "RegionId": "me-dubai-1", "RegionName": "UAE Central (Dubai)",
                    "ConsumedQuantity": pc["ConsumedQuantity"], "ConsumedUnit": "Hours",
                    "PricingQuantity": pc["PricingQuantity"], "PricingUnit": "vCPU-Hours",
                    "Tags": common.tag_sparsity(rng, wl.tags),
                    "CommitmentDiscountId": pc["CommitmentDiscountId"],
                    "CommitmentDiscountStatus": pc["CommitmentDiscountStatus"],
                    "x_CompartmentId": "ocid1.compartment.oc1..demoanalytics",
                })
                rows.append(r)
        # account-level non-usage charges (Tax/Purchase/Credit/Refund)
        rows.extend(_non_usage_rows(
            rng, common.FAKE_OCI_TENANCY, "DEMO-OCI-Tenancy", bps, bpe, cps, cpe,
            "Oracle Cloud Infrastructure", "Oracle", "USD",
            {"x_CompartmentId": ""}))

    # OCI generative-AI rows (per-model) — spread across the window.
    for d in range(days):
        cd = dt.date(2026, 6, 1) + dt.timedelta(days=d)
        bps, bpe, cps, cpe = _period(cd)
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
    # Demo volume: default 30 days so the dashboard reads as credible.
    # Override with FOCUS_GEN_DAYS (e.g. 3 for a fast hand-traceable run).
    days = int(os.environ.get("FOCUS_GEN_DAYS", "30"))
    paths = {}
    for name, gen in (("aws", generate_aws), ("azure", generate_azure), ("oci", generate_oci)):
        rows, cols = gen(days=days)
        paths[name] = _write(os.path.join(out, f"focus_{name}.csv"), rows, cols)
    return paths


if __name__ == "__main__":
    for name, path in write_all().items():
        import csv as _csv
        n = sum(1 for _ in open(path)) - 1
        print(f"{name:6s} -> {path}  ({n} rows)")
