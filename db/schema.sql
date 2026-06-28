-- ============================================================
-- enbd-multicloud-finops-poc :: focus DB schema
-- ============================================================
-- THROWAWAY POC SCHEMA. NOT A PRODUCTION MIGRATION.
--
-- Tables:
--   focus_costs        --- normalized FOCUS v1.3 cost rows from all clouds
--   miq_utilization    --- per-VM CPU/mem % from ManageIQ metric_rollups
--   miq_onprem_cost    --- on-prem chargeback costs in FOCUS-ish shape (slice 6)
--   resource_join_map  --- the resolved cloud<->MIQ<->on-prem identity map
--   load_metadata      --- one row per loader run, for audit
--
-- Column names mirror FOCUS v1.3 display names in PascalCase. Postgres
-- folds unquoted identifiers to lowercase so we use snake_case for
-- columns (with the FOCUS column name as a comment) to keep both worlds
-- happy without forcing quoted identifiers everywhere.
--
-- Idempotent: drops tables if they exist, then recreates.
-- ============================================================

DROP TABLE IF EXISTS resource_join_map CASCADE;
DROP TABLE IF EXISTS miq_utilization   CASCADE;
DROP TABLE IF EXISTS miq_onprem_cost   CASCADE;
DROP TABLE IF EXISTS focus_costs       CASCADE;
DROP TABLE IF EXISTS load_metadata     CASCADE;


-- ------------------------------------------------------------
-- focus_costs --- the FOCUS fact table
-- ------------------------------------------------------------
-- Sourced from out/normalizer/focus_combined.csv (slice 2 output).
-- Per GOTCHA F-1 we use the v1.3 column names (no deprecated 'provider'
-- / 'publisher').
-- Per GOTCHA F-2 service_category is NOT NULL --- normalizer guarantees
-- this; if the loader sees a NULL it must reject the row.
CREATE TABLE focus_costs (
    row_id              BIGSERIAL PRIMARY KEY,
    source              TEXT NOT NULL,                     -- 'aws' | 'azure' | 'oci' (private debug field)
    -- The REGISTRY source instance this row came from (dispatcher's cfg.source_id).
    -- Enables per-source incremental load (DELETE WHERE source_id=X; INSERT) so
    -- an upload replaces only its own partition instead of TRUNCATEing the whole
    -- warehouse (GOTCHA W-15). Nullable for rows loaded by the bulk seed path.
    source_id           TEXT,

    -- Account / period
    billing_account_id      TEXT,                          -- BillingAccountId
    billing_account_name    TEXT,                          -- BillingAccountName
    sub_account_id          TEXT,                          -- SubAccountId
    sub_account_name        TEXT,                          -- SubAccountName
    billing_period_start    DATE,                          -- BillingPeriodStart
    billing_period_end      DATE,                          -- BillingPeriodEnd
    charge_period_start     TIMESTAMPTZ,                   -- ChargePeriodStart
    charge_period_end       TIMESTAMPTZ,                   -- ChargePeriodEnd

    -- Charge / cost
    charge_category         TEXT,                          -- ChargeCategory
    charge_description      TEXT,                          -- ChargeDescription
    charge_frequency        TEXT,                          -- ChargeFrequency
    billed_cost             NUMERIC(20,6),                 -- BilledCost (in billing_currency)
    -- Normalized to a single reporting currency (USD) at load via a recorded
    -- FX rate, so cross-provider SUMs are valid (GOTCHA H-1). Never SUM
    -- billed_cost across providers directly — it mixes AED and USD.
    billed_cost_usd         NUMERIC(20,6),
    fx_rate_to_usd          NUMERIC(18,8),                 -- rate used (audit)
    effective_cost          NUMERIC(20,6),                 -- EffectiveCost
    list_cost               NUMERIC(20,6),                 -- ListCost
    contracted_cost         NUMERIC(20,6),                 -- ContractedCost
    billing_currency        TEXT NOT NULL,                 -- BillingCurrency (mandatory in FOCUS)
    pricing_currency        TEXT,                          -- PricingCurrency

    -- Provider (v1.3 names)
    service_provider_name   TEXT,                          -- ServiceProviderName
    invoice_issuer_name     TEXT,                          -- InvoiceIssuerName

    -- Service / SKU
    service_category        TEXT NOT NULL,                 -- ServiceCategory (mandatory + closed set, GOTCHA F-2)
    service_subcategory     TEXT,                          -- ServiceSubcategory
    service_name            TEXT,                          -- ServiceName
    sku_id                  TEXT,                          -- SkuId
    sku_meter               TEXT,                          -- SkuMeter (Bedrock model id is in here for AWS rows)
    sku_price_id            TEXT,                          -- SkuPriceId

    -- Resource (the join keys, see GOTCHA J-1)
    resource_id             TEXT,                          -- ResourceId
    resource_name           TEXT,                          -- ResourceName
    resource_type           TEXT,                          -- ResourceType
    region_id               TEXT,                          -- RegionId
    region_name             TEXT,                          -- RegionName
    availability_zone       TEXT,                          -- AvailabilityZone

    -- Consumption
    consumed_quantity       NUMERIC(20,6),                 -- ConsumedQuantity
    consumed_unit           TEXT,                          -- ConsumedUnit
    pricing_quantity        NUMERIC(20,6),                 -- PricingQuantity
    pricing_unit            TEXT,                          -- PricingUnit

    -- Tags as a JSON blob (Azure/AWS/OCI all hand us these differently)
    tags                    JSONB,                         -- Tags

    -- Commitment discount identity (Savings Plans / Reserved / OCI commitments).
    -- Why EffectiveCost can diverge from BilledCost/ListCost (FinOps coverage story).
    commitment_discount_id      TEXT,                          -- CommitmentDiscountId
    commitment_discount_status  TEXT,                          -- CommitmentDiscountStatus

    -- Provider x_ extension columns preserved as JSONB (GOTCHA H-9) — e.g.
    -- AWS x_Discounts/x_Operation/x_ServiceCode. Portable FOCUS consumers
    -- ignore it; AWS-specific analysis can read it.
    extensions              JSONB,

    -- Idempotency key (GOTCHA H-6): stable hash of the row's identifying
    -- fields. A full truncate+reload is idempotent on its own; this enables
    -- safe INCREMENTAL upserts (ON CONFLICT) when the load becomes append-mode.
    idempotency_key         TEXT,

    loaded_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_focus_costs_idem ON focus_costs(idempotency_key);

CREATE INDEX idx_focus_costs_resource_id        ON focus_costs(resource_id);
CREATE INDEX idx_focus_costs_service_category   ON focus_costs(service_category);
CREATE INDEX idx_focus_costs_billing_period     ON focus_costs(billing_period_start);
CREATE INDEX idx_focus_costs_source             ON focus_costs(source);
CREATE INDEX idx_focus_costs_source_id          ON focus_costs(source_id);
CREATE INDEX idx_focus_costs_sku_meter          ON focus_costs(sku_meter);
-- Bedrock model rollups need fast filter+group on (service_category, sku_meter)
CREATE INDEX idx_focus_costs_ai                 ON focus_costs(service_category, sku_meter)
    WHERE service_category = 'AI and Machine Learning';


-- ------------------------------------------------------------
-- miq_utilization --- per-VM CPU/mem % rollups
-- ------------------------------------------------------------
-- Sourced from the appliance's metric_rollups (resource_type='VmOrTemplate'
-- per GOTCHA J-3). One row per (miq_vm_id, hourly_timestamp).
CREATE TABLE miq_utilization (
    miq_vm_id               BIGINT NOT NULL,
    timestamp               TIMESTAMPTZ NOT NULL,
    capture_interval        INTEGER NOT NULL,              -- seconds; 3600 = hourly
    cpu_usage_pct           DOUBLE PRECISION,              -- cpu_usage_rate_average
    mem_usage_pct           DOUBLE PRECISION,              -- mem_usage_absolute_average
    resource_name           TEXT,
    loaded_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (miq_vm_id, timestamp)
);

CREATE INDEX idx_miq_util_timestamp ON miq_utilization(timestamp);


-- ------------------------------------------------------------
-- miq_onprem_cost --- on-prem chargeback rows shaped like FOCUS
-- ------------------------------------------------------------
-- Slice 6 will populate this from the ManageIQ chargeback module
-- (per GOTCHA G-5, chargebacks ARE enabled on this appliance).
-- For now the table exists so the join is complete.
CREATE TABLE miq_onprem_cost (
    row_id                  BIGSERIAL PRIMARY KEY,
    miq_vm_id               BIGINT NOT NULL,
    charge_period_start     TIMESTAMPTZ NOT NULL,
    charge_period_end       TIMESTAMPTZ NOT NULL,
    chargeback_rate_id      BIGINT,                        -- which rate produced this (G-5)
    billed_cost             NUMERIC(20,6) NOT NULL,
    billing_currency        TEXT NOT NULL,
    service_category        TEXT NOT NULL,                 -- typically 'Compute' or 'Storage'
    service_name            TEXT,
    sub_account_id          TEXT,                          -- business unit / cost center
    notes                   TEXT,
    loaded_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_miq_onprem_vm        ON miq_onprem_cost(miq_vm_id);
CREATE INDEX idx_miq_onprem_period    ON miq_onprem_cost(charge_period_start);


-- ------------------------------------------------------------
-- resource_join_map --- the FOCUS<->MIQ resolved join
-- ------------------------------------------------------------
-- Sourced from out/join/resource_join_map.csv (slice 3 output).
-- Each row carries ONE of these statuses; the web layer presents each
-- status with a different banner per SPEC §3.5 + GOTCHA J-6.
CREATE TABLE resource_join_map (
    row_id                      BIGSERIAL PRIMARY KEY,
    status                      TEXT NOT NULL,
    -- Allowed: 'matched', 'unmatched_focus_only', 'unmatched_miq_only',
    --          'ambiguous', 'no_resource_id'
    CONSTRAINT chk_status CHECK (status IN (
        'matched', 'unmatched_focus_only', 'unmatched_miq_only',
        'ambiguous', 'no_resource_id'
    )),

    focus_source                TEXT,                      -- 'aws'/'azure'/'oci' or '' for MIQ-only
    focus_resource_id           TEXT,
    focus_service_category      TEXT,
    focus_billed_cost_sum       NUMERIC(20,6) NOT NULL DEFAULT 0,
    focus_row_count             INTEGER NOT NULL DEFAULT 0,

    miq_vm_id                   TEXT,                      -- TEXT not BIGINT --- "1,2,3" for ambiguous
    miq_vm_name                 TEXT,
    miq_vendor                  TEXT,
    miq_uid_ems                 TEXT,
    miq_ems_ref                 TEXT,
    join_key_used               TEXT,                      -- 'uid_ems' | 'ems_ref' | ''
    notes                       TEXT,

    loaded_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_join_status              ON resource_join_map(status);
CREATE INDEX idx_join_focus_resource_id   ON resource_join_map(focus_resource_id);
CREATE INDEX idx_join_focus_source        ON resource_join_map(focus_source);


-- ------------------------------------------------------------
-- load_metadata --- audit one row per loader run
-- ------------------------------------------------------------
CREATE TABLE load_metadata (
    run_id                  BIGSERIAL PRIMARY KEY,
    started_at              TIMESTAMPTZ NOT NULL,
    finished_at             TIMESTAMPTZ,
    focus_rows_loaded       INTEGER,
    join_rows_loaded        INTEGER,
    miq_util_rows_loaded    INTEGER,
    miq_onprem_rows_loaded  INTEGER,
    notes                   TEXT
);
