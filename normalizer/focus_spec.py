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


# FOCUS v1.3 column IDs we emit (subset that matters for this PoC).
# Sourced from focus-finops MCP list_columns v1-3 on 2026-06-25.
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
