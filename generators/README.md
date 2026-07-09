# generators/

Synthetic, deliberately-messy fixtures for the FOCUS↔ManageIQ join PoC.

> This directory is the **first** layer in SPEC §2's build-by-risk order. The data here is engineered to break naive joins so the gotchas surface before the EBA team hits them in production.

## Why messy data?

A clean fixture set hides the join problem and makes the PoC pointless. SPEC §3.1 mandates the following messiness recipes; this directory implements all of them:

| Recipe | Where implemented | Gotcha this exposes |
|---|---|---|
| Resource naming differs across providers for the same workload | `common.Workload.name_in_provider()` | J-1 (cross-provider naming) |
| On-prem rows with no cloud-style ResourceId | Workloads 5 & 6 in `common.WORKLOADS` | "no join key" path |
| Null/blank `ServiceCategory` | `aws_cur.py` blank-ProductName row, Azure rows with Azure-shaped `ServiceFamily` | F-2 (ServiceCategory closed-set) |
| Azure cost-export column quirks | All of `azure_cost_export.py` | F-1 (deprecation) and J-1 (Azure ARM-path join) |
| Duplicate and late-arriving records | `aws_cur.py` duplicate row + 2026-05-31 late row | idempotent-load discipline |
| Bedrock AI line items (per-model) | `aws_cur.py` BEDROCK_MODELS loop | SPEC requirement #1 demo data |
| Mixed currencies (AED/USD) | All cloud generators; `usd_to_aed()` in `common` | FOCUS BillingCurrency vs PricingCurrency |
| Obviously synthetic data | `DEMO-` prefix, fake account IDs, `???.invalid` emails | Leadership data-sensitivity concern (SPEC §0) |

## What each file does

| File | Output | What it emits |
|---|---|---|
| `common.py` | (none — imported) | The single source of truth: 7 workloads, 3 on-prem, mix of clouds, with provider-asymmetric IDs. |
| `aws_cur.py` | `out/generators/aws_cur.csv` | CUR2-style CSV. EC2 hourly + Bedrock per-model + duplicate + late-arriving + blank-product messiness. |
| `azure_cost_export.py` | `out/generators/azure_cost.csv` | Azure cost-export CSV with PascalCase columns, ARM-path ResourceId, JSON Tags-in-a-cell, AED/USD split. |
| `oci_usage.py` | `out/generators/oci_usage.csv` | OCI usage report CSV with OCID resources and an empty-compartmentId row. |
| `miq_vmdb_seed.py` | `out/generators/miq_vmdb_seed.sql` | SQL to seed `vms` / `hardwares` / `metric_rollups` on the appliance. Uses fixed ID range (90000+) so it's idempotent. |

## How to run

```bash
# From repo root
python3 -m generators.aws_cur
python3 -m generators.azure_cost_export
python3 -m generators.oci_usage
python3 -m generators.miq_vmdb_seed

# Apply the MIQ seed to the appliance
docker cp out/generators/miq_vmdb_seed.sql manageiq_appliance:/tmp/miq_seed.sql
docker exec manageiq_appliance psql -U postgres -d vmdb_production -f /tmp/miq_seed.sql
```

## What the data is engineered to break

After running all four generators, you have:

- **3 AWS workloads** in `aws_cur.csv` whose `lineItem/ResourceId` matches the **`vms.uid_ems`** of three rows in MIQ. Naive `vms.name = focus.ResourceName` joins will return zero matches because the names differ (`Payments Gateway` vs `payments-gateway`).
- **2 Azure workloads** in `azure_cost.csv` whose `ResourceId` is the **full ARM path** — this matches **`vms.ems_ref`**, **NOT `vms.uid_ems`**. A loader that joins both providers on `uid_ems` will silently drop all Azure rows.
- **1 OCI workload** whose `lineItem/resourceId` matches `vms.uid_ems` (OCID).
- **2 on-prem-only workloads** (Core Banking Legacy, Mainframe Bridge) with NO cloud cost rows. They exist only in MIQ; the join must report them as "MIQ-only, no FOCUS row" rather than silently dropping them.
- **1 cross-cloud workload** (KYC Service) with TWO MIQ rows (one AWS-attributed, one Azure-attributed) — the join must reconcile two-rows-one-workload.

## Reproducibility

Everything uses `common.RNG_SEED` (`20260625`). Same seed → same output. Re-running generators produces byte-identical files, so the gotchas reproduce.

## When to throw this away

The whole `generators/` directory is throwaway. ENBD's real data will arrive via:

- Real CUR exports from AWS into an S3 bucket (FOCUS-conformant or not).
- Azure cost-management export to a storage account.
- OCI usage reports to OCI Object Storage.
- ManageIQ inventory via the real refresh path (with real cloud creds — gotcha G-8).

The normalizer, on the other hand, is permanent. It should consume real exports the day creds arrive. The generators just unblock that work.
