# AnyBank Multi-Cloud FinOps PoC — FOCUS + ManageIQ + Bedrock

**Status:** Design-approved spec, ready for implementation
**Author context:** Koushal Dutt (AWS, MENAT) for the AnyBank "Multi-Cloud Cost Optimization" engagement
**Date:** 2026-06-24

---

## 0. Read this first — what this PoC is and is NOT

**This is a throwaway de-risking spike, not a product.** Its primary purpose is to let the AWS team
(Koushal) walk into the planned 3-day **EBA innovation sprint** already knowing where the AnyBank
engineering team will get stuck — so the team can build the real thing
**themselves** during the EBA, faster.

Therefore:

- **The most valuable artifact is `GOTCHAS.md`, not the running app.** Every non-obvious problem hit
  during the build (data mapping quirks, join failures, API contract surprises, residency friction)
  gets written down there as it happens. That file is the real handoff.
- **Code optimizes for legibility and teachability**, not elegance or performance. Each module is a
  self-contained lesson the AnyBank team will re-implement by hand. Comment generously; prefer obvious
  structure over clever abstractions.
- **Do NOT** position or build this as production software: no real cloud credentials, no real AnyBank
  data, no multi-tenancy, no production auth, no CMP/Terraform remediation actions.

### Engagement context (from May 7 & May 18, 2026 meetings)

- AnyBank runs **ManageIQ behind their CMP (Cloud Management Platform)** as the front-end for cloud
  deployments. They have **already integrated Azure cost data** into ManageIQ but have **not** aligned
  it to FOCUS. Their **chargeback module exists but is NOT enabled**. They built a **Node.js cost module**.
- Agreed PoC architecture (what the customer has already seen): ingest cost extracts from **AWS (real),
  Azure + OCI (synthetic)** → normalize to **FOCUS** → **PostgreSQL in a container** → **presentation
  page** (sits on CMP; a separate page in the demo) → **isolated AWS Bedrock** AI insights layer
  (explicitly optional — FOCUS works without it).
- Existing ManageIQ cost calc = **vCPU + memory + per-GB cost over 4 years**, monthly/daily burndown.
- **Open decision:** host the DB on-prem vs. in AWS (data-transfer cost). → keep everything portable.
- **Security signals to respect:** Ahmed asked about prompt injection / system prompts / caching;
  Leadership flagged data sensitivity (competitors must not see their data). Bedrock data residency in MENAT
  is constrained (Global inference profile only from me-central-1) — treat the AI layer carefully.
- **Strategic framing (leadership):** be the **first bank in MENA** to do multi-cloud FOCUS customization.
  → correctness matters more than flash. A confidently-wrong number is worse than no number.

---

## 1. Requirements & honest FOCUS verdicts

Leadership raised four requirements. Their true feasibility against the **FOCUS spec** (verified against
focus.finops.org v1.3/v1.4 column inventory and the normative Service Category section):

| # | Requirement | FOCUS verdict | How this PoC serves it |
|---|---|---|---|
| 1 | **AI cost & usage** by cloud and by model | ✅ **Native** | `ServiceCategory='AI and Machine Learning'` (mandatory, non-null, normative allowed value), `ServiceName`, `SkuId`/`SkuMeter`/`SkuPriceId`, `ConsumedQuantity`/`ConsumedUnit`, `ServiceProviderName` |
| 2 | **Resource utilization %** + cost (VMs/containers) | ⚠️ **Partial** — cost yes, **utilization % NOT in FOCUS** | Cost from FOCUS; CPU/mem **% from ManageIQ**, joined on resource identity |
| 3 | **Cloud vs on-prem** cost comparison + recharge | ⚠️ **Conditional** — FOCUS is provider-agnostic but does **not** source on-prem data | Cloud cost from FOCUS; on-prem cost from **ManageIQ chargeback**, normalized into FOCUS shape |
| 4 | **Carbon footprint** | ❌ **Out of scope for FOCUS** (no carbon/emissions/energy column exists through v1.4) | **Stub view** + roadmap pointing to AWS **CCFT** + per-cloud feeds + on-prem model |

**The PoC must SHOW these verdicts in the UI**, not hide them. Each view carries an honest banner stating
where its data comes from and what FOCUS can/can't do. This is the answer to leadership's actual question.

---

## 2. Architecture

```
   Cost sources                Normalization          Store              Presentation        AI (isolated)
┌──────────────────┐
│ AWS  (synthetic) │──┐
│ Azure (synthetic)│──┼─▶ FOCUS Normalizer ─▶ PostgreSQL  ─▶ Web presentation ──▶ Bedrock NL-query
│ OCI  (synthetic) │──┘   (→ FOCUS 1.x)       (container)     page (CMP-style)      service (optional)
└──────────────────┘                              ▲
┌──────────────────┐                              │
│ ManageIQ (real   │──▶ MIQ Collector ────────────┘
│ appliance, synth │    (REST API: VMs, containers,
│ data loaded in)  │     CPU/mem %, chargeback, tags)
└──────────────────┘
```

- Everything runs via **docker-compose** so it is portable (on-prem OR AWS) — respects the open hosting decision.
- **ManageIQ is a REAL appliance** you stand up yourself, loaded with **synthetic data**. The integration
  hits its **real REST API** (`/api/vms`, `/api/container_*`, metrics rollups, chargeback) — this is how we
  find the true API gotchas without needing AnyBank's instance or real data.
- The **AI layer is a separate container** that only reads Postgres. FOCUS must fully function with it
  stopped (directly answers "is the AI mandatory?" — no).

### Build order — BY RISK (hardest/most-uncertain first, because the gotchas are the deliverable)

1. **FOCUS ↔ ManageIQ join** on deliberately-messy synthetic data ← the landmine; build first
2. **Provider-native → FOCUS normalizer** (lead with the **Azure** mapping — it's their real near-term pain)
3. **On-prem + cloud cost views and utilization view** (#2, #3)
4. **AI cost view** (#1 — the easy, native one)
5. **Bedrock NL-query layer** (last; canned queries first, then guarded free-text)
6. **Carbon stub + CCFT roadmap** (#4)

> Rationale: requirement #1 "just works" and teaches us nothing. The uncertainty — and thus the de-risk
> value — lives in the join and the Azure mapping. Spend effort there.

---

## 3. Components (each independently testable)

### 3.1 Synthetic data generators  `/generators`
Emit **provider-native** (NOT pre-FOCUS) cost exports for AWS, Azure, OCI + a loader that seeds the
ManageIQ appliance.

**CRITICAL — data must be deliberately MESSY, not clean.** Clean data makes the join look trivial and
teaches nothing, then the real gotcha ambushes the team during the EBA. Inject, on purpose:
- Resource naming that **differs across providers** for the same logical workload
- **On-prem rows with no cloud-style ResourceId** (the #3 join problem)
- **Null/blank `ServiceCategory`** that the normalizer must map
- Azure cost-export quirks (its column names/structure differ from AWS CUR / FOCUS)
- Duplicate and late-arriving records
- A **Bedrock AI line item** (per-model: e.g. Claude Sonnet input/output tokens) so #1 has real rows
- Mixed currencies (AED/USD) to exercise `BillingCurrency` vs `PricingCurrency`

Data must be **obviously synthetic** (fake account IDs, "DEMO-" prefixes) so no one mistakes it for AnyBank
data — this defuses the leadership data-sensitivity concern during the demo.

### 3.2 FOCUS normalizer  `/normalizer`
Maps each provider-native format → FOCUS columns. Validates conformance: `ServiceCategory` mandatory &
non-null & one of the normative allowed values; required cost columns present; types correct.
Lead with and most-thoroughly document the **Azure → FOCUS** mapping.
Emit a **validation report** (rows passed/failed, which columns were unmappable) — itself a gotcha source.

### 3.3 ManageIQ collector  `/miq_collector`
Talks to the **real ManageIQ REST API**. Pulls: VM/container inventory, **CPU/memory utilization %**
(C&U metrics rollups), **chargeback** rates/costs (note: module must be **enabled** in your appliance —
document that as a gotcha), and **tags/identifiers** for the join.
Document the exact API contract used (endpoints, auth, pagination, rollup intervals) in `GOTCHAS.md`.

### 3.4 PostgreSQL schema  `/db`
- `focus_costs` — FOCUS fact table (cost & usage rows)
- `miq_utilization` — per-resource CPU/mem/disk %
- `miq_onprem_cost` — on-prem chargeback costs (FOCUS-shaped)
- `resource_join_map` — the resolved cloud↔MIQ↔on-prem identity mapping (materializes the hard join)
Keyed to make the join explicit and inspectable.

### 3.5 Presentation page  `/web`
CMP-style single page, four views (§1). Each view shows its **honest data-source banner**. Lightweight —
this is a demo surface, not a product UI.

### 3.6 Bedrock NL-query service  `/ai` (optional, isolated)
FastAPI service, text-to-SQL over the FOCUS schema. **Built last.**
- **Canned/parameterized queries first**; free-text only after guardrails work.
- Guardrails: fixed system prompt, prompt-injection mitigation, read-only SQL allowlist, **no training/
  caching of input data**, every answer shows the SQL it ran + the rows (no unverified narration).
- A wrong cost figure with confident AI narration is the worst outcome for a bank — fail closed.
- Document Bedrock **region/residency** posture (me-central-1 → Global inference profile only) as a gotcha
  for the data-sensitivity discussion.

---

## 4. Tech choices

- **Data layer:** Python (generators, normalizer, collector). Note: AnyBank's existing module is Node.js —
  call this out as a divergence; Python chosen for data-tooling clarity and EBA teachability.
- **DB:** PostgreSQL 16 (container).
- **Web:** lightweight Python web UI (Flask or FastAPI + minimal HTML/Chart.js). No heavy frontend build.
- **AI:** AWS Bedrock (Claude) via isolated FastAPI service.
- **Orchestration:** docker-compose, single command up. ManageIQ appliance runs as its own container/pod.

---

## 5. Deliverables / definition of done

1. `docker-compose up` brings the whole stack (incl. ManageIQ) to life on one machine.
2. All four views render with honest data-source banners; verdicts (✅/⚠️/⚠️/❌) visible.
3. Requirement #1 (AI cost by cloud & model) is **correct and demo-ready**.
4. The FOCUS↔ManageIQ join works on **messy** synthetic data (and its failure modes are documented).
5. **`GOTCHAS.md`** populated with every non-obvious issue hit, framed as guidance for the EBA team.
6. A short **`EBA-BACKLOG.md`**: what the AnyBank team builds during the sprint, in order, with the gotchas flagged.
7. Bedrock layer can be switched off and the rest still works.

---

## 6. Explicitly OUT of scope (YAGNI)

CMP/Terraform remediation, live cloud credentials, real AnyBank data, real carbon feeds, production auth /
multi-tenancy, performance tuning, the 4-year burndown calc (unless trivially free from existing data).

---

## 7. Known risks / assumptions to watch (carry into `GOTCHAS.md`)

- **The join is the whole game** for #2/#3 and is genuinely hard (entity resolution across AWS/Azure/
  on-prem; on-prem has no cloud ResourceId). Clean synthetic data will hide this — that's why §3.1 mandates messy data.
- **ManageIQ chargeback is disabled by default** — you must enable it in your appliance; AnyBank will have to
  enable it too. Document the steps.
- **Carbon stub may read as "AWS couldn't do it"** to leadership — the roadmap framing (CCFT for the AWS
  slice is real and could show real-ish data) must be explicit so it lands as honesty, not a gap.
- **Bedrock NL layer is high-visibility / high-risk** — keep it last and optional; canned queries first.
- **This is a reference spike, not AnyBank's product** — state that to the customer so the build isn't
  mistaken for an AWS-owned deliverable.
