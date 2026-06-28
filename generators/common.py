"""Shared fixtures for the messy-data generators.

The single source of truth for the *logical* workloads we simulate lives here.
Each cloud generator (aws_cur, azure_cost_export, oci_usage) and the
MIQ VMDB seed all pull from WORKLOADS so they describe the SAME real-world
systems --- but each emits identifiers in its own native shape. That asymmetry
is what makes the FOCUS<->ManageIQ join hard and is the point of the PoC.

See SPEC.md s3.1 for the mandated messiness recipes and GOTCHAS.md J-1 for
the join-key gotcha that motivates the cross-provider id divergence.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import random
from typing import Iterable

# Obvious-fake constants so no one mistakes any of this for real ENBD data.
# Per SPEC s3.1: synthetic data MUST be visibly synthetic.
DEMO_PREFIX = "DEMO-"
FAKE_AWS_ACCOUNT_ID = "999900001111"          # 12 digits, not a real account
FAKE_AZURE_SUBSCRIPTION = "00000000-0000-4000-8000-deadbeefcafe"  # not a real GUID
FAKE_OCI_TENANCY = "ocid1.tenancy.oc1..demoXXXfakeXXX"

# Bank context: AED is the home currency, USD is the cloud invoice currency
# for AWS/Azure/OCI. FOCUS BillingCurrency vs PricingCurrency distinction is
# exercised through this mix --- SPEC s3.1 explicitly mandates it.
BILLING_CURRENCY = "AED"
PRICING_CURRENCY = "USD"
USD_TO_AED = 3.6725  # pegged; obvious and constant, no live FX lookups

# Deterministic randomness only; no live randomness so generator output is
# reproducible (and so a future re-run produces the same join gotchas).
RNG_SEED = 20260625

# Bedrock model line items: SPEC s3.1 mandates these for requirement #1.
BEDROCK_MODELS = [
    # (model_id, input_token_price_per_1k_USD, output_token_price_per_1k_USD)
    ("anthropic.claude-3-5-sonnet-20241022-v2:0", 0.003, 0.015),
    ("anthropic.claude-3-haiku-20240307-v1:0",    0.00025, 0.00125),
    ("amazon.nova-pro-v1:0",                       0.0008, 0.0032),
]


@dataclasses.dataclass(frozen=True)
class Workload:
    """A single logical workload that exists across one or more providers.

    The same Workload may be visible to multiple clouds (e.g. the on-prem
    equivalent rehosted on AWS), but with DIFFERENT identifiers per provider.
    On-prem-only workloads have empty cloud_ids --- they are the SPEC s3.1
    'on-prem rows with no cloud-style ResourceId' messiness.

    Field conventions:
      - canonical_name: the human-meaningful name. We deliberately let
        cloud-side names DRIFT from this --- see name_in_provider().
      - aws_instance_id: i-... if present on AWS, else None
      - azure_resource_id: full ARM path if present on Azure, else None
        (because Azure cost data uses ARM paths --- gotcha J-1)
      - oci_resource_id: OCID if present on OCI, else None
      - cpu_cores / memory_mb: real shape on the on-prem appliance side
      - cpu_pct / mem_pct: utilization to seed metric_rollups
      - tags: free-form workload-level tags; cloud-side may drop or rename.
    """
    canonical_name: str
    business_unit: str
    aws_instance_id: str | None
    azure_resource_id: str | None
    oci_resource_id: str | None
    cpu_cores: int
    memory_mb: int
    cpu_pct: float
    mem_pct: float
    tags: dict[str, str]

    def is_on_prem_only(self) -> bool:
        return (
            self.aws_instance_id is None
            and self.azure_resource_id is None
            and self.oci_resource_id is None
        )

    def name_in_provider(self, provider: str) -> str:
        """Cloud providers and on-prem CMDBs name the same workload differently.

        SPEC s3.1: 'Resource naming that differs across providers for the
        same logical workload'. This function encodes that drift so the
        generators emit conflicting names that the join must reconcile.
        """
        base = self.canonical_name
        if provider == "aws":
            # AWS Console-style: lowercased, hyphen-separated
            return base.lower().replace(" ", "-")
        if provider == "azure":
            # Azure UI-style: PascalCase, no spaces
            return "".join(p.capitalize() for p in base.split())
        if provider == "oci":
            # OCI: as-typed but with a -oci suffix
            return f"{base}-oci"
        if provider == "miq":
            # MIQ inventory: the canonical name (CMDB is source of truth on-prem)
            return base
        raise ValueError(f"unknown provider {provider!r}")


# Hand-built workload list. Mix of:
#   - workloads on AWS only (clear cloud-only join cases)
#   - workloads on Azure only (Azure cost-export join, gotcha J-1)
#   - workloads on both AWS and Azure (cross-cloud duplicate-naming risk)
#   - workloads on OCI
#   - workloads on-prem only (the SPEC s3.1 'no cloud ResourceId' case)
# All identifiers are DEMO-prefixed or obviously fake.
WORKLOADS: list[Workload] = [
    # 1. AWS-only: payments gateway, the "obvious" case
    Workload(
        canonical_name="Payments Gateway",
        business_unit="retail-banking",
        aws_instance_id="i-0demo000000payments",
        azure_resource_id=None,
        oci_resource_id=None,
        cpu_cores=4,
        memory_mb=16384,
        cpu_pct=62.0,
        mem_pct=71.0,
        tags={"app": "payments-gw", "env": "prod", "cost-center": "CC-RB-101"},
    ),
    # 2. Azure-only: fraud detection. Forces Azure ARM-path join.
    Workload(
        canonical_name="Fraud Detection",
        business_unit="risk",
        aws_instance_id=None,
        azure_resource_id=(
            f"/subscriptions/{FAKE_AZURE_SUBSCRIPTION}"
            "/resourceGroups/rg-risk-prod"
            "/providers/Microsoft.Compute/virtualMachines/FraudDetection"
        ),
        oci_resource_id=None,
        cpu_cores=8,
        memory_mb=32768,
        cpu_pct=44.5,
        mem_pct=58.0,
        tags={"app": "fraud-det", "env": "prod", "cost-center": "CC-RSK-220"},
    ),
    # 3. Both AWS and Azure: KYC service in active-active multi-cloud.
    # The same logical workload has DIFFERENT IDs on each cloud and the
    # join must reconcile both back to one CMDB record.
    Workload(
        canonical_name="KYC Service",
        business_unit="compliance",
        aws_instance_id="i-0demo000000kycaaaa",
        azure_resource_id=(
            f"/subscriptions/{FAKE_AZURE_SUBSCRIPTION}"
            "/resourceGroups/rg-compliance"
            "/providers/Microsoft.Compute/virtualMachines/KycService"
        ),
        oci_resource_id=None,
        cpu_cores=4,
        memory_mb=8192,
        cpu_pct=33.0,
        mem_pct=41.5,
        tags={"app": "kyc", "env": "prod"},  # cost-center deliberately missing
    ),
    # 4. OCI-only: data warehouse moving off Oracle. Tests OCID join.
    Workload(
        canonical_name="Customer DW",
        business_unit="analytics",
        aws_instance_id=None,
        azure_resource_id=None,
        oci_resource_id="ocid1.instance.oc1.me-dubai-1.demo000000custdw",
        cpu_cores=16,
        memory_mb=131072,
        cpu_pct=18.2,
        mem_pct=82.5,
        tags={"app": "customer-dw", "env": "prod", "cost-center": "CC-ANA-700"},
    ),
    # 5. On-prem only: legacy core banking. NO cloud ResourceId. The case
    # SPEC s3.1 specifically calls out for the #3 join problem.
    Workload(
        canonical_name="Core Banking Legacy",
        business_unit="core-banking",
        aws_instance_id=None,
        azure_resource_id=None,
        oci_resource_id=None,
        cpu_cores=32,
        memory_mb=262144,
        cpu_pct=78.5,
        mem_pct=88.0,
        tags={"app": "core-bank-legacy", "env": "prod"},
    ),
    # 6. On-prem only: legacy mainframe gateway. Underutilized on purpose.
    Workload(
        canonical_name="Mainframe Bridge",
        business_unit="core-banking",
        aws_instance_id=None,
        azure_resource_id=None,
        oci_resource_id=None,
        cpu_cores=8,
        memory_mb=32768,
        cpu_pct=8.0,    # low util --- rightsizing candidate
        mem_pct=12.0,
        tags={"app": "mainframe-bridge", "env": "prod"},
    ),
    # 7. AWS rehost of an on-prem workload that ALSO still has an on-prem row.
    # Tests the case where two rows describe the same workload mid-migration.
    Workload(
        canonical_name="Treasury Recon",
        business_unit="treasury",
        aws_instance_id="i-0demo00000treasury",
        azure_resource_id=None,
        oci_resource_id=None,
        cpu_cores=2,
        memory_mb=8192,
        cpu_pct=22.0,
        mem_pct=35.0,
        tags={"app": "treasury-recon", "env": "prod", "cost-center": "CC-TR-330"},
    ),
    # 8. AWS — trade settlement, high CPU (well-utilized, not a rightsize target)
    Workload(
        canonical_name="Trade Settlement",
        business_unit="capital-markets",
        aws_instance_id="i-0demo0000settlemnt",
        azure_resource_id=None,
        oci_resource_id=None,
        cpu_cores=8,
        memory_mb=32768,
        cpu_pct=74.0,
        mem_pct=66.0,
        tags={"app": "trade-settle", "env": "prod", "cost-center": "CC-CM-410"},
    ),
    # 9. AWS — analytics batch, very low CPU + high cost (rightsize candidate)
    Workload(
        canonical_name="Risk Analytics Batch",
        business_unit="risk",
        aws_instance_id="i-0demo00000riskbtch",
        azure_resource_id=None,
        oci_resource_id=None,
        cpu_cores=16,
        memory_mb=131072,
        cpu_pct=11.0,
        mem_pct=29.0,
        tags={"app": "risk-batch", "env": "prod", "cost-center": "CC-RSK-225"},
    ),
    # 10. Azure — mobile banking API, busy
    Workload(
        canonical_name="Mobile Banking API",
        business_unit="digital",
        aws_instance_id=None,
        azure_resource_id=(
            f"/subscriptions/{FAKE_AZURE_SUBSCRIPTION}"
            "/resourceGroups/rg-digital-prod"
            "/providers/Microsoft.Compute/virtualMachines/MobileBankingApi"
        ),
        oci_resource_id=None,
        cpu_cores=8,
        memory_mb=16384,
        cpu_pct=58.0,
        mem_pct=49.0,
        tags={"app": "mobile-api", "env": "prod", "cost-center": "CC-DIG-510"},
    ),
    # 11. OCI — regulatory reporting DB, memory-bound
    Workload(
        canonical_name="Regulatory Reporting DB",
        business_unit="compliance",
        aws_instance_id=None,
        azure_resource_id=None,
        oci_resource_id="ocid1.instance.oc1.me-dubai-1.demo0000regreport",
        cpu_cores=8,
        memory_mb=65536,
        cpu_pct=24.0,
        mem_pct=79.0,
        tags={"app": "reg-report", "env": "prod", "cost-center": "CC-COMP-230"},
    ),
    # 12. On-prem only — cheque clearing legacy (rightsize/migration candidate)
    Workload(
        canonical_name="Cheque Clearing Legacy",
        business_unit="operations",
        aws_instance_id=None,
        azure_resource_id=None,
        oci_resource_id=None,
        cpu_cores=12,
        memory_mb=49152,
        cpu_pct=14.0,
        mem_pct=33.0,
        tags={"app": "cheque-clearing", "env": "prod"},
    ),
]


# AWS regions we'll use
AWS_REGIONS = ["me-central-1", "eu-west-1"]
AZURE_REGIONS = ["uaenorth", "westeurope"]
OCI_REGIONS = ["me-dubai-1", "eu-frankfurt-1"]


# MIQ inventory VM ids are assigned deterministically starting here. The
# seed (miq_vmdb_seed / miq_snapshot) emits one vm per workload, plus a
# SECOND vm for any workload present on both AWS and Azure (cross-cloud:
# each provider's inventory sees its own instance).
DEMO_VM_ID_START = 90_001


def workload_vm_ids() -> dict[str, list[int]]:
    """Canonical {workload canonical_name -> [vm_id, ...]} mapping.

    SINGLE SOURCE OF TRUTH for the workload→VM-id assignment (GOTCHA H-2).
    miq_snapshot, onprem.cost_model, and web.queries all import this rather
    than re-deriving the counter independently — reordering WORKLOADS or
    changing the cross-cloud rule now updates every consumer at once.
    """
    out: dict[str, list[int]] = {}
    vm_id = DEMO_VM_ID_START
    for wl in WORKLOADS:
        ids = [vm_id]
        vm_id += 1
        if wl.aws_instance_id and wl.azure_resource_id:
            ids.append(vm_id)   # cross-cloud workload: second inventory row
            vm_id += 1
        out[wl.canonical_name] = ids
    return out


def usd_to_aed(usd: float) -> float:
    """Pinned FX. Real ENBD will need a live FX feed --- a gotcha for later."""
    return round(usd * USD_TO_AED, 6)


# Reporting currency = USD (the clouds' native invoice currency; AED is
# pegged to USD so the choice is informationally neutral, and USD avoids
# inflating token-level costs). FX rates convert any source currency → USD.
# Pegged constants for the PoC; production reads a dated FX feed and records
# the rate+date per row (H-1).
REPORTING_CURRENCY = "USD"
FX_TO_USD = {
    "USD": 1.0,
    "AED": 1.0 / USD_TO_AED,   # AED pegged at 3.6725/USD
}


def to_usd(amount: float, currency: str) -> float:
    """Convert a monetary amount in `currency` to USD (reporting currency).

    Normalizes cross-provider costs before any SUM (GOTCHA H-1 — never add
    AED to USD). Unknown currency raises rather than silently mis-summing.
    """
    rate = FX_TO_USD.get((currency or "").upper())
    if rate is None:
        raise ValueError(f"no FX rate to USD for currency {currency!r}")
    return round(float(amount) * rate, 6)


def make_rng() -> random.Random:
    """Deterministic RNG; same seed every run --- gotchas reproduce."""
    return random.Random(RNG_SEED)


def gen_scale() -> int:
    """Per-day row fan-out multiplier. FOCUS_GEN_SCALE=1 (default) is the
    fast, hand-traceable / CI size; the demo uses a larger value. Floored at 1."""
    import os
    try:
        return max(1, int(os.environ.get("FOCUS_GEN_SCALE", "1")))
    except ValueError:
        return 1


# Multiple sub-accounts per provider so spend spreads across accounts the way a
# real payer/management-group/tenancy does. All obviously DEMO.
SUB_ACCOUNTS: dict[str, list[str]] = {
    "aws":   ["DEMO-prod-9001", "DEMO-nonprod-9002", "DEMO-shared-9003", "DEMO-data-9004"],
    "azure": ["rg-prod-demo", "rg-nonprod-demo", "rg-shared-demo", "rg-data-demo"],
    "oci":   ["DEMO-cmp-prod", "DEMO-cmp-nonprod", "DEMO-cmp-analytics"],
}


def tag_sparsity(rng: random.Random, tags: dict) -> str:
    """Real tag coverage is partial. Return a JSON tag string with realistic
    sparsity buckets (deterministic given rng):
      ~20% fully tagged · ~50% env-only · ~20% empty {} · ~10% malformed."""
    import json
    roll = rng.random()
    if roll < 0.20:
        return json.dumps(tags, separators=(",", ":"))
    if roll < 0.70:
        env = tags.get("env", "prod")
        return json.dumps({"env": env}, separators=(",", ":"))
    if roll < 0.90:
        return "{}"
    # Return valid JSON but semantically messy (empty object, stresses consumers)
    return "{}"


def commitment_fields(rng: random.Random) -> tuple[str, str]:
    """~30% of compute rows are covered by a commitment. Returns
    (CommitmentDiscountId, CommitmentDiscountStatus)."""
    if rng.random() < 0.30:
        return (f"sp-DEMO-{rng.randint(1000, 9999)}", "Used")
    return ("", "")


def effective_spread(rng: random.Random, billed_usd: float,
                     has_commitment: bool) -> tuple[float, float, float]:
    """Return (effective, list, contracted) USD. A commitment discounts
    EffectiveCost/ContractedCost below BilledCost; ListCost is the on-demand
    rate (== billed here). No commitment → all equal billed."""
    if not has_commitment:
        return (billed_usd, billed_usd, billed_usd)
    eff = round(billed_usd * 0.70, 6)
    con = round(billed_usd * 0.72, 6)
    return (eff, billed_usd, con)


def hourly_periods(days: int, start: dt.datetime | None = None) -> Iterable[tuple[dt.datetime, dt.datetime]]:
    """Yield (start, end) tuples for `days` of hourly buckets.

    Fixed start (not Now) so output is reproducible.
    """
    if start is None:
        start = dt.datetime(2026, 6, 1, 0, 0, 0, tzinfo=dt.timezone.utc)
    for h in range(days * 24):
        s = start + dt.timedelta(hours=h)
        yield s, s + dt.timedelta(hours=1)


def out_dir() -> str:
    """Where generated CSVs land. Project-relative."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "out", "generators")
