"""FOCUS v1.3 spec constants used by the normalizer.

Verified against the live FOCUS spec via the focus-finops MCP server
(spec page v1-3, section 3.1.55.5 'Content Constraints' for ServiceCategory).
DO NOT modify these from memory --- re-fetch from the spec.
"""

# Closed set of allowed ServiceCategory values per FOCUS v1.3 s3.1.55.5.
# Values are *case-sensitive* in the spec; the normalizer must emit exactly
# these strings, not casual variants like "ML" or "Compute Services".
SERVICE_CATEGORIES_V1_3 = {
    "AI and Machine Learning",
    "Analytics",
    "Business Applications",
    "Compute",
    "Databases",
    "Developer Tools",
    "Multicloud",
    "Identity",
    "Integration",
    "Internet of Things",
    "Management and Governance",
    "Media",
    "Migration",
    "Mobile",
    "Networking",
    "Security",
    "Storage",
    "Web",
    "Other",
}

# Constraint summary (also from 3.1.55.5):
#   - Column type: Dimension
#   - Feature level: Mandatory
#   - Allows nulls: False
#   - Data type: String
SERVICE_CATEGORY_REQUIRED = True
SERVICE_CATEGORY_ALLOWS_NULL = False

# Closed set of allowed ChargeCategory values per FOCUS v1.3. Case-sensitive.
# Real exports are NOT all "Usage" — taxes, commitment purchases, credits,
# refunds and adjustments all appear and break naive SUM(BilledCost).
CHARGE_CATEGORIES_V1_3 = {
    "Usage",
    "Purchase",
    "Tax",
    "Credit",
    "Adjustment",
    "Refund",
}


# FOCUS v1.3 column IDs we emit. Sourced + feature-levels verified against the
# focus-finops MCP (list_columns v1-3 + spec chunk 7 'Content constraints').
#
# HISTORY (FIN-2): this list was originally a hand-picked "subset that matters
# for the PoC" and OMITTED the UNIT-PRICE columns — which silently made
# cross-provider price comparison impossible (you can't ask "is an AWS m5.xlarge
# pricier than a comparable Azure box" without a per-unit price). FOCUS DOES
# standardize this (ListUnitPrice/ContractedUnitPrice per PricingUnit; spec
# §2.7 Cost Comparison, §2.21 Unit Price Comparison). We now emit the full set
# of Mandatory columns plus the high-value Conditional ones (unit prices,
# pricing category) so the synthetic data is real-shaped, not a shortcut.
FOCUS_COLUMNS_V1_3 = [
    # Identity / period
    "BillingAccountId",
    "BillingAccountName",
    "BillingPeriodStart",
    "BillingPeriodEnd",
    "ChargePeriodStart",
    "ChargePeriodEnd",
    "SubAccountId",
    "SubAccountName",
    # Charge / cost
    "ChargeCategory",
    "ChargeDescription",
    "ChargeFrequency",
    "BilledCost",
    "EffectiveCost",
    "ListCost",
    "ContractedCost",
    "BillingCurrency",
    "PricingCurrency",
    # Unit prices (FIN-2) — the columns that make cost COMPARISON possible.
    # ListUnitPrice = published price per ONE PricingUnit of the SKU, pre-discount,
    # in BillingCurrency (spec 3.1.39). ContractedUnitPrice = negotiated unit
    # price (3.1.33). PricingCategory = On-Demand | Committed | Dynamic | Other
    # (the rate basis). Invariant we hold: ListUnitPrice * PricingQuantity ≈ ListCost.
    "ListUnitPrice",
    "ContractedUnitPrice",
    "PricingCategory",
    # Provider
    "ServiceProviderName",   # v1.3 replacement for deprecated 'Provider'
    "InvoiceIssuerName",     # v1.3 replacement for deprecated 'Publisher'
    # Service / SKU
    "ServiceCategory",       # closed set --- see SERVICE_CATEGORIES_V1_3
    "ServiceSubcategory",
    "ServiceName",
    "SkuId",
    "SkuMeter",
    "SkuPriceId",
    # Resource
    "ResourceId",
    "ResourceName",
    "ResourceType",
    "RegionId",
    "RegionName",
    "AvailabilityZone",
    # Consumption
    "ConsumedQuantity",
    "ConsumedUnit",
    "PricingQuantity",
    "PricingUnit",
    # Tagging
    "Tags",
    # Commitment discounts (Savings Plans / Reserved / OCI commitments). Their
    # presence is why EffectiveCost can differ from BilledCost/ListCost.
    "CommitmentDiscountId",
    "CommitmentDiscountStatus",
]

# Closed set for PricingCategory (FOCUS v1.3 §3.1.40). The rate basis a charge
# was priced under — distinct from ChargeCategory.
PRICING_CATEGORIES_V1_3 = {
    "On-Demand",
    "Committed",
    "Dynamic",
    "Other",
}


# Deprecated → current column aliases (version-leveling, FIN-3). Real FOCUS data
# spans versions: the FinOps Foundation's own 1.0 sample uses ProviderName /
# PublisherName, which v1.3 renamed to ServiceProviderName / InvoiceIssuerName
# (deprecated in 1.3, removed in 1.4). A pipeline that claims to handle "real
# FOCUS" must accept the older names and level them to the target, or it rejects
# the reference dataset. Map is {deprecated_name: current_v1_3_name}.
DEPRECATED_COLUMN_ALIASES = {
    "ProviderName": "ServiceProviderName",   # deprecated v1.3 (§3.1.47)
    "PublisherName": "InvoiceIssuerName",    # deprecated v1.3 (§3.1.48)
}


def level_to_v1_3(row: dict) -> dict:
    """Return a copy of `row` with deprecated column names leveled to their
    v1.3 names (FIN-3). A deprecated value only fills the current column when
    the current one is absent/empty — a row carrying both keeps the current."""
    out = dict(row)
    for old, new in DEPRECATED_COLUMN_ALIASES.items():
        if old in out and not (out.get(new) or "").strip():
            out[new] = out[old]
    return out
