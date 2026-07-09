# Production architecture — AnyBank Multi-Cloud FinOps

**Status:** Design for review. Not yet approved, not yet built.
**Date:** 2026-06-26.
**Author context:** Koushal Dutt (AWS, MENAT) for AnyBank.
**Relationship to the PoC:** the PoC in this repo is the throwaway de-risking
spike. This document describes what the production system looks like *after*
the EBA sprint, and which PoC components survive the transition unchanged.

> Read `SPEC.md` for the PoC scope and `GOTCHAS.md` for the landmines that
> shaped these decisions. The gotcha IDs referenced below (B-1, D-1, J-1,
> LM-1, P-1, P-2, …) live in `GOTCHAS.md`.

---

## 0a. Design north star — connect-and-run

**AnyBank's only job in production is to connect data sources. The platform does
the rest.** No code change to onboard a new AWS account, Azure subscription,
OCI tenancy, or ManageIQ appliance — each is a row of configuration plus a
credential in Secrets Manager. The platform discovers the export, normalizes
it to FOCUS, joins it, and surfaces it.

This means the architecture is a **connector framework**, not a hard-wired
pipeline:

```
AnyBank admin UI / config:  "Add data source"
   ├─ type: aws-cur | azure-export | oci-usage | manageiq
   ├─ where: S3 path / blob URL / object-store prefix / MIQ endpoint
   ├─ credential ref: secrets-manager ARN  (never a raw secret)
   └─ schedule: how often to pull
            │
            ▼
   Connector registry (a DB table + a dispatcher)
            │
   ┌────────┴─────────┐
   ▼                  ▼
 source adapter   →  normalizer (FOCUS v1.3)  →  Aurora  →  dashboard
 (one per type)      (the PoC's normalizer/)
```

What makes this achievable: the PoC already proved the *hard* part — the
per-provider FOCUS mappings (`normalizer/`) and the asymmetric join (J-1). The
production work is wrapping those in a **registry + scheduler + adapter
interface** so adding a source is data, not a deploy. Each `*_to_focus.py`
module becomes a registered adapter keyed by source type; the loader already
honors env-based connection config (D-1). The connector contract:

```
class SourceAdapter(Protocol):
    source_type: str                      # "aws-cur" | "azure-export" | ...
    def discover(self, cfg) -> list[Export]      # find new files since last run
    def fetch(self, export, creds) -> RawRows     # pull (TLS-verified, G-6)
    def to_focus(self, rows) -> list[FocusRow]    # the PoC normalizer, lifted
    def validate(self, rows) -> ValidationReport  # conformance gate (F-2)
```

Adding OCI generative-AI cost mid-flight (which we just did in the PoC by
editing two files) becomes, in production, a mapping-table entry the adapter
already consults — no redeploy. That's the connect-and-run promise made
concrete.

**Boundary of the promise:** "connect and run" covers *cost + utilization +
on-prem*. It does NOT auto-magic carbon (no FOCUS column, needs four separate
feeds — C-1..C-5) or the AI layer (optional, residency-gated — B-1). Those are
deliberate opt-ins, not silent defaults — honesty over turnkey theatre.

---

## 0. The one-paragraph version

FOCUS-conformant cost exports from AWS, Azure, and OCI land in object storage,
get normalized to FOCUS v1.3, and are stored in **Aurora PostgreSQL**. ManageIQ
(on-prem, Red Hat lineage) is collected over a private link for inventory,
utilization, and on-prem chargeback. A **ROSA** (managed OpenShift on AWS)
web tier serves the four-view dashboard, reachable only from AnyBank's network
and authenticated through AnyBank's existing IdP. An **isolated Bedrock** service
provides optional NL-query, fail-closed, behind a SQL guardrail. Everything is
region-parameterized: **us-east-1 for the pilot on synthetic data,
me-central-1 (UAE) for production on real data** (P-1).

---

## 1. Decisions taken (and why)

| Axis | Decision | Rationale |
|---|---|---|
| Platform host | **AWS**, region-parameterized | Managed Aurora/Bedrock; ManageIQ stays where it is (on-prem). |
| Region — pilot | **us-east-1** | Simpler, best Bedrock availability, synthetic data only. |
| Region — prod | **me-central-1 (UAE)** | Real cost data residency (P-1). Decision is explicit, not default. |
| FOCUS store | **Aurora PostgreSQL** | Direct lift of the PoC schema + loader + queries. Right-sized for one bank's cost volume (millions of rows/month). Migration friction ≈ zero (D-1). |
| Web tier | **ROSA** (managed OpenShift) | AnyBank is an OpenShift shop; ManageIQ is Red Hat lineage (P-2). PoC image runs unchanged. |
| Access | **Private + AnyBank SSO** | No public exposure. Private ALB/Route, AnyBank IdP (SAML/OIDC), WAF. Bank-appropriate. |
| AI layer | **Isolated Bedrock, fail-closed** | FOCUS works fully with AI off (SPEC §3.6). SQL guardrail enforced at parser level (B-3). |

Alternatives considered and rejected:
- **S3 + Athena** for storage — correct at petabyte scale; overkill for one
  bank's cost data and would force porting the PoC's Postgres SQL to Trino
  dialect. Revisit only if cost rows exceed ~hundreds of millions/month.
- **Raw EKS** for compute — worst of both worlds for an OpenShift shop (P-2).
- **ECS Fargate** — viable lighter alternative; documented in §6 as the
  fallback if this stays a standalone dashboard rather than joining the
  OpenShift estate.

---

## 2. Architecture

```
 PROVIDERS (cost exports, FOCUS-native where available)
 ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
 │ AWS  CUR 2.0 │   │ Azure cost   │   │ OCI usage    │
 │ → FOCUS      │   │ export       │   │ report       │
 └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
        │ (S3 / blob / object-store delivery, scheduled)
        ▼                  ▼                  ▼
 ┌─────────────────────────────────────────────────────┐
 │  INGEST + NORMALIZE   (the PoC's normalizer/, hardened)│
 │  • land raw in S3 (versioned, KMS-encrypted)          │
 │  • provider-native → FOCUS v1.3 (lead: Azure, J-1)    │
 │  • row-level validation report (conformance gate)     │
 └───────────────────────┬───────────────────────────────┘
                         ▼
 ┌─────────────────────────────────────────────────────┐
 │  AURORA POSTGRESQL  (FOCUS warehouse)                 │
 │  focus_costs · resource_join_map · miq_utilization ·  │
 │  miq_onprem_cost · load_metadata     (PoC schema)     │
 │  Multi-AZ · KMS at rest · automated backups · PITR    │
 └──────────▲────────────────────────────┬──────────────┘
            │                            │
 ┌──────────┴────────────┐    ┌──────────▼──────────────┐
 │ MIQ COLLECTOR          │    │  WEB TIER (ROSA)         │
 │ • ManageIQ REST over   │    │  • FastAPI image (PoC)   │
 │   private link (G-2)   │    │  • 4 views + NL query    │
 │ • inventory, C&U %,    │    │  • Route (OpenShift)     │
 │   chargeback (G-5)     │    │  • AnyBank SSO (OIDC/SAML)  │
 │ • on-prem, no cloud    │    │  • WAF, private only     │
 │   ResourceId (J-1)     │    └──────────┬──────────────┘
 └────────────────────────┘               │
        ▲                                  ▼
 ┌──────┴──────────┐          ┌─────────────────────────┐
 │ ManageIQ        │          │ BEDROCK NL-QUERY (opt)   │
 │ (ON-PREM, RH)   │          │ • isolated, fail-closed  │
 │ LM-1: keep here │          │ • SQL allowlist (B-3)    │
 └─────────────────┘          │ • global. profile (B-1)  │
                              └─────────────────────────┘
```

---

## 3. What survives from the PoC unchanged

The de-risking worked: most of the PoC is the production data plane, not a
throwaway.

| PoC component | Production fate |
|---|---|
| `normalizer/` (Azure/AWS/OCI → FOCUS v1.3) | **Survives.** Hardened with real exports; mapping tables grow. |
| `db/schema.sql` | **Survives.** Same DDL on Aurora. |
| `db/loader.py` | **Survives.** Honors `FOCUS_PG_*` env → Aurora is a connection-string swap (D-1). |
| `join/resource_join_map.py` | **Survives.** The asymmetric join-key logic (J-1) is the hard-won core. |
| `onprem/cost_model.py` | **Survives as fallback;** replaced by real ManageIQ chargeback reads where rates exist (O-1). |
| `web/` (FastAPI + templates) | **Survives.** Same image; deploy manifest changes (Route vs local). |
| `ai/` (Bedrock + SQL guard) | **Survives.** Guardrail (`sql_guard.py`) was designed against production constraints (B-3); like all PoC code it still needs AnyBank AppSec review before production use. |
| `generators/` (synthetic data) | **Retired** for production; kept for CI fixtures + the EBA teaching path. |
| `join/miq_snapshot.py` | **Retired;** replaced by the live MIQ collector once an appliance with the LM-1 memory cap is available. |

### 3a. The new production code (the connect-and-run layer)

The PoC proved the data transforms; production wraps them in a connector
framework. New components the EBA team builds:

| New component | Job |
|---|---|
| `connectors/registry` (DB table + API) | One row per connected source: type, location, credential ref, schedule, last-run watermark. The "Add data source" surface writes here. |
| `connectors/dispatcher` (scheduled worker) | Reads the registry, invokes the right adapter per source on schedule, records watermarks, emits the validation report. |
| `connectors/adapters/*` | Thin wrappers around the PoC's `normalizer/*_to_focus.py` implementing the `SourceAdapter` contract (§0a). Adding a source type = adding an adapter; adding a source *instance* = a registry row, no deploy. |
| Admin UI "Connect a source" | Form → registry row + Secrets Manager credential. The only thing AnyBank touches to onboard a cloud account. |

---

## 4. Security & residency posture (the bank's first questions)

- **Residency (P-1):** production in me-central-1. Raw cost data never leaves
  UAE. Bedrock in me-central-1 is `global.` inference-profile only (B-1) — the
  AI layer's residency must be explicitly accepted by legal or the AI layer
  stays off in production (it's optional by design).
- **Encryption:** KMS (customer-managed keys) at rest on Aurora + S3; TLS in
  transit everywhere, including the MIQ collector's private link (no
  `verify=False`, ever — G-6).
- **Secrets:** ManageIQ token + Aurora credentials in AWS Secrets Manager with
  rotation. Never in env files committed to git. The default `admin:smartvm`
  must be rotated (G-1).
- **Network:** platform in private subnets. ManageIQ reached over
  Direct Connect / VPN / PrivateLink, not the public internet. Web tier
  private, AnyBank-IdP-gated, WAF in front.
- **Multi-account:** ingest, warehouse, and serving in separate AWS accounts
  under AnyBank's Organization, with SCPs. Cost-data account is the crown jewel —
  tightest blast radius.
- **AI fail-closed:** every model-produced SQL is parser-validated against the
  four-table allowlist (B-3); a wrong-cost-with-confident-narration is the
  worst outcome for a bank (SPEC §0). The AI layer can be disabled wholesale
  with one env flag and the rest of the platform is unaffected.

---

## 5. Phased roadmap

| Phase | Data | Region | Compute | Goal |
|---|---|---|---|---|
| **0 — PoC (this repo)** | synthetic | local / us-east-1 | docker-compose | de-risk the join + mappings; produce GOTCHAS.md |
| **1 — Pilot** | synthetic + 1 real AWS account (read-only CUR) | us-east-1 | ROSA (or ECS Fargate) | prove the pipeline on one real cost feed, AnyBank SSO, private access |
| **2 — Production** | all real (AWS+Azure+OCI+on-prem) | **me-central-1** | ROSA | full multi-cloud, Multi-AZ Aurora, rotation, WAF, SCPs |
| **3 — Optimize** | + Bedrock NL-query, carbon feeds | me-central-1 | ROSA | AI layer (if legal clears B-1), CCFT/EID carbon streams (C-1..C-5) |

Each phase is a go/no-go gate. Phase 1 does NOT carry real data into us-east-1
beyond a single read-only AWS account that AnyBank explicitly designates, and even
that is a conscious call — see P-1.

---

## 6. Compute: ROSA vs ECS Fargate (the live decision)

**ROSA** if this joins AnyBank's OpenShift platform estate (recommended given P-2):
the team operates it with the `oc`/Operators/Routes they already use; ManageIQ's
Red Hat lineage makes this the natural home; the PoC image runs unchanged.

**ECS Fargate** if this stays a standalone, low-traffic internal dashboard:
no cluster to run, scales near-zero, cheapest ops burden, same image. The
trade is it lives outside the OpenShift platform the rest of AnyBank's apps use.

The web tier is **stateless** — all state is in Aurora — so this decision is
reversible and does not affect any other component. Pick ROSA to align with the
platform estate; pick Fargate to minimize standalone ops. Either way the
container image and the `FOCUS_PG_*` contract are identical.

---

## 7. Open decisions for AnyBank (not ours to make)

1. **Production region** — confirm me-central-1 for real data (P-1). Legal sign-off.
2. **ROSA vs ECS Fargate** (§6) — depends on whether this joins the OpenShift estate.
3. **Chargeback ownership** — keep computation in ManageIQ (read its output, G-5)
   or own it in Python (`onprem/cost_model.py`, O-1). Finance + platform call.
4. **AI layer in production** — on (accept B-1 residency) or off (optional by design).
5. **Carbon** — when/whether to wire the four real feeds (C-1..C-5); needs the
   DC team's PUE sign-off (C-5) and per-provider feed enablement.
6. **Identity** — confirm the IdP fronting ManageIQ is the one to reuse for SSO.

---

## 8. What this document is not

- Not an approved design — it's a starting point for the AnyBank architecture review.
- Not a cost estimate — that follows once §7 decisions land.
- Not a commitment to build — the PoC proves feasibility; AnyBank's team builds
  the real thing during/after the EBA sprint, using this as the target picture.
