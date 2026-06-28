# Real MVP — Spec 1: Config-driven ingestion (upload-first) + realistic synthetic data

**Date:** 2026-06-28
**Status:** Approved (brainstorming) — pending implementation plan
**Supersedes premise of:** the throwaway-spike framing in `SPEC.md` §0, for this and the
follow-on specs. The non-negotiables in `CLAUDE.md`/`SPEC.md` (synthetic-only, USD
normalization, honest banners, visible join, FOCUS conformance) still hold verbatim.

## 0. Why this exists (the pivot)

The PoC proved the hard parts (the FOCUS↔ManageIQ join, the Azure→FOCUS mapping, the
mixed-currency normalization). The new intent is to turn that spike into a **real,
production-grade MVP** that:

- runs production-grade **on synthetic data** (the operator is the AWS team; the only
  thing fake is the data — everything else is production-quality, deploy-ready code);
- becomes a **reusable product** other customers can adopt **config-driven, single-tenant
  per deploy** (clone → configure → deploy their own instance; explicitly NOT multi-tenant
  SaaS, no SSO, no per-tenant isolation logic);
- is verified locally on the compose stack now; actual ROSA+Aurora apply happens later
  from a credentialed session (dodges the CX-4 EC2-role permissions wall).

This spec is **Spec 1 of a sequence**. It is the ingestion spine. Decomposition:

| Spec | Scope | Status |
|------|-------|--------|
| **1 (this)** | Config-driven **upload** ingestion + realistic synthetic data + connector SDK contract | **active** |
| 2 | Live ManageIQ REST collector (replaces `join/miq_snapshot.py`) | deferred |
| 3 | "Deploy your own" packaging (clone → configure → deploy a customer instance) | deferred |
| 4 | Production hardening: real app auth, observability, CI/CD to ROSA+Aurora | deferred |
| — | API-pull connectors (`AwsCostExplorerSource` etc.) as working impls | deferred (contract defined here) |

## 1. Scope & goal

Turn the current "seed a CSV off disk" pipeline into a real, config-driven ingestion
product where **nothing in the verification path is fake except the data itself**.

In scope:

1. **Realistic synthetic FOCUS exports** — generators upgraded to look like real provider
   output (volume, breadth, charge-category mix, commitment discounts, tag sparsity). These
   are both the test fixtures and the demo data.
2. **A real upload ingestion path** — a user uploads a provider FOCUS export through the
   Connect UI → validated as FOCUS-conformant → normalized → loaded → joined → on the
   dashboard. This is the production code path, exercised verbatim on synthetic data.
3. **Config-driven onboarding** — registering a new source is a registry/config action,
   **zero code change**; the conformance validator confirms the result.
4. **A documented connector-source contract (SDK surface)** — the `SourceAdapter` Protocol
   is the reusability surface; `UploadSource` is the first real implementation; API-pull
   sources are named, registered, stubbed.

Honors (unchanged): synthetic + obviously-fake data (`DEMO-` prefixes, fake account IDs),
USD normalization (never SUM mixed currency), honest data-source banners, the join stays
visible, FOCUS conformance validator must pass.

## 2. Architecture (deltas against today's framework)

The framework already has the right shape:
`registry → dispatcher → adapter.discover()/normalize() → focus_combined.csv → loader →
join → dashboard`, with the contract (`SourceConfig`/`DiscoveredExport`/`NormalizeResult`/
`SourceAdapter`) already the reusability surface. Spec 1 changes three things and adds one.
The fact that the framework boundary absorbs this change with no contract churn IS the proof
the boundary is correct.

**Delta 1 — `discover()` becomes real for the upload path.** Today every adapter calls
`_local_export(cfg)` (treats `cfg.location` as a fixed CSV). New **`UploadSource`** whose
`location` is an **upload inbox directory** (`out/uploads/<source_id>/`); `discover()` lists
files in that inbox newer than a watermark and returns one `DiscoveredExport` per file. The
watermark makes re-running idempotent (no re-ingest of an already-loaded file). `normalize()`
is unchanged — an uploaded native-FOCUS export maps identically regardless of arrival path.
**This is the "real production code path" promise:** the only difference between this and a
future S3 `discover()` is *where the bytes come from*; map/load/join/validate are untouched.

**Delta 2 — a real upload endpoint.** `POST /connect/upload` accepts a file + `source_id`,
validates FOCUS-conformant header *before* accepting (reject early), writes to that source's
inbox, then runs `dispatcher.run()`. The UI gets a real file picker. The honesty banner
becomes accurate: "you uploaded this file; it was validated as FOCUS and ingested."

**Delta 3 — realistic synthetic generators** (see §3).

**Addition — the deferred API-pull contract.** `AwsCostExplorerSource` / `AzureExportSource`
as **registered-but-stubbed** adapters: real `source_type`, real class, `discover()`/
`normalize()` raise `NotImplementedError("deferred to Spec-N")` with a docstring describing
the intended AWS Data-Exports→S3 / Azure Cost-Management flow. This nails the contract as the
extension surface without building creds-dependent code we can't verify on synthetic.

**Unchanged:** contract dataclasses, `focus_combined.csv` shape, `db/loader.py`, the join,
the conformance validator, USD normalization.

## 3. Realistic synthetic data

Upgrade `generators/focus_native.py` (+ `generators/common.py`) from thin/hand-traceable to
real-shaped, keeping every row obviously synthetic and the join visible. Six dimensions:

1. **Volume & breadth** — tens of thousands of rows: multiple accounts/subscriptions/
   compartments under one payer, dozens of services, a realistic long-tail SKU/meter mix.
   Exercises aggregation, pagination, load time honestly.
2. **Full ChargeCategory mix** — not all `Usage`: add `Tax`, `Purchase` (commitments),
   `Credit`, `Refund`, `Adjustment`. These break naive `SUM(BilledCost)` and stress the
   currency/conformance guards. **Included in this slice** (approved).
3. **Commitment-discount rows** — `CommitmentDiscountId/Category/Type/Status` populated for a
   slice of compute, so `EffectiveCost ≠ BilledCost ≠ ListCost` realistically. **Included.**
4. **Tag sparsity & messiness** — partial tag coverage: fully tagged, `env`-only, untagged,
   occasional malformed/empty tag JSON. Drives the untagged-spend story, stresses the parser.
5. **Currency realism** — keep Azure AED-billing / USD-pricing split (B-6/B-7 bug class);
   enough mixed-currency volume that a USD-normalization regression is visibly wrong.
6. **Controlled dirty rows, scaled** — keep deliberate defects (null ServiceCategory,
   duplicates, out-of-set category, malformed ResourceId) as a small **known fraction**, so
   validator drop/warn counts are non-trivial and assertable.

**Constraints:** deterministic (seeded `common.make_rng()`) so tests assert exact counts;
parameterized by existing `FOCUS_GEN_DAYS` plus a new `FOCUS_GEN_SCALE` knob (small for
CI/hand-tracing, full for the demo).

**Join intact:** ResourceId still carries the provider-native join key (AWS instance id /
Azure ARM / OCI OCID per J-1); the ManageIQ snapshot keeps matching keys, including the
deliberate J-6 unmatched residual, scaled proportionally so join-distribution stays honest.

## 4. Validation, error handling & honesty

Three layers, each fails safe:

1. **Upload-time (reject early)** — before a file enters the inbox, `POST /connect/upload`
   checks: parses as CSV, header contains FOCUS mandatory columns, non-empty. Fail → HTTP 400
   with a specific reason; file not written, nothing ingested.
2. **Row-level (exists, keep)** — `focus_native_to_focus.map_row` flags fatal rows
   (null/out-of-set ServiceCategory, empty BillingCurrency) into the report and drops rather
   than loads. Realistic dirty rows exercise this; tests assert drop counts.
3. **Post-load conformance (exists, keep as the gate)** — `web.queries.focus_conformance()`
   is the authoritative check; `/reseed` runs it as a required step. It is the definition of
   "done correctly."

**Honesty surfaces:**
- Connect banner → accurate: uploaded files are validated and ingested; synthetic-generator
  sources remain as demo seeds; **API-pull sources shown but disabled** with a "coming in a
  later release" label (a stubbed connector must never look live — SPEC §0 failure mode).
- Dashboard view banners unchanged (the data is still synthetic; upload doesn't change that).

**Dispatcher fail-soft:** one source failing (bad adapter, unreadable file, a
`NotImplementedError` from a stubbed API-pull source) must NOT abort the run — record it in
the per-source summary (`status: error` + reason); other sources still ingest. Today's
dispatcher skips `no-adapter`/`no-exports` gracefully; extend the same pattern by wrapping
`normalize()` in try/except.

**Known fix (logged to GOTCHAS):** the dispatcher currently `sys.exit(1)` on a non-conformant
ServiceCategory *after* writing the CSV. For an interactive upload this must become a returned
structured error the UI can show, not a process exit.

## 5. Testing & definition of done

**Test additions (extend `tests/`, same pytest pattern):**

1. **Upload validation** (pure logic) — conformant CSV passes; missing-mandatory-column,
   empty, non-CSV each reject with the right reason and write nothing.
2. **Generator realism** (pure logic) — fixed seed + small `FOCUS_GEN_SCALE`: assert exact
   row counts, presence of each ChargeCategory, commitment rows where `EffectiveCost ≠
   BilledCost`, tag-sparsity distribution, known dirty-row fraction.
3. **Dispatcher fail-soft** (pure logic) — one poison + one good source → good ingests,
   poison recorded `status: error`, no process exit.
4. **Data-integrity additions** (DB-backed, skip without Postgres) — after seeding realistic
   data, B-6/B-7 currency + join guards still hold at volume; conformance passes.

**CI:** existing `.github/workflows/ci.yml` flow (py_compile → schema → seed → pytest with
real Postgres) unchanged; new tests slot in; CI seeds at small scale.

**Definition of done (evidenced, not asserted):**
- `/reseed` passes end to end: rebuild → seed realistic data → pytest green → conformance
  `conformant=True` → all routes 200.
- A file uploaded through `/connect/upload` (a generated export) is validated, ingested, and
  visible on the dashboard — demonstrated, not just unit-tested.
- A stubbed API-pull source appears in the Connect UI, clearly disabled/"later release," and
  raising it in the dispatcher is caught (fail-soft), not fatal.
- The connector SDK contract is documented: what `discover()`/`normalize()` must do, what
  `SourceConfig` fields mean, how a new source type is added — written so a customer could
  author their own adapter.
- Non-obvious findings captured in GOTCHAS.md.

**Out of scope (anti-drift):** live S3/API fetch bodies; ManageIQ live collector (Spec 2);
deploy-your-own packaging (Spec 3); production auth/observability/CD (Spec 4).
