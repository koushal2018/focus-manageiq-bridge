# EBA sprint backlog &mdash; what ENBD's team builds

**3-day innovation sprint.** This is the ordered work plan, calibrated to the
gotchas this PoC surfaced. Each item links to the relevant `GOTCHAS.md`
entries; read those before estimating.

> Build order mirrors **SPEC §2 by risk, not by feature order**. The join is
> the landmine; everything else is downstream of getting that right.

---

## Day 0 (pre-sprint, the half-day before kickoff)

### B-0. Provision the compute hosts properly
- **Owner:** infra / platform
- **Done when:** every dev/demo host has ≥ 24 GB RAM, the ManageIQ container
  is started with `--memory=6g --restart=no`, Docker daemon mask sequence
  from `LM-2` is in the runbook.
- **Reason:** **LM-1 is the most important operational gotcha in this PoC.**
  The appliance OOM-killed our dev box and the VS Code remote crash signature
  was uninformative. If the ENBD team hits this in front of leadership, the
  demo dies silently. Solve it before kickoff.
- **GOTCHAS:** LM-1, LM-2.

### B-0.5. Decide the **synthetic-data approach** before configuring providers
- **Owner:** lead engineer
- **Done when:** the team has chosen between (a) seed the VMDB directly via
  SQL/Rails console, or (b) stand up LocalStack-style fake providers.
- **Reason:** Even synthetic providers in ManageIQ go through real cloud
  SDKs and fail real auth (G-8). The team will waste a half day chasing
  "why does my synthetic Azure provider get an Azure SDK NPE" if this
  isn't decided up front. This PoC chose (a); option (b) is more
  faithful to "real provider refresh" but adds a day of setup.
- **GOTCHAS:** G-8.

---

## Day 1 — the join (the hardest thing)

### B-1. Stand up Postgres in its own container (NOT inside the appliance)
- **Done when:** docker-compose has a `postgres:16` service with a hard
  memory limit, the `focus` DB exists, `db/schema.sql` from this PoC
  applies clean.
- **Reason:** Reusing the appliance's Postgres is the PoC's tactical
  shortcut (D-1). The EBA team's real deployment must keep the FinOps
  DB portable on-prem vs AWS (SPEC §2). Splitting it out is a 30-minute
  change to the loader config.
- **GOTCHAS:** D-1, D-3.

### B-2. Implement the **FOCUS↔MIQ resource_join_map** with all five status buckets
- **Done when:** Cloud cost rows are joined to MIQ inventory using
  `vms.uid_ems` for AWS/OCI and `vms.ems_ref` for Azure (the **asymmetric
  join key is the landmine** &mdash; J-1). Every cost row lands in one of:
  `matched`, `unmatched_focus_only`, `unmatched_miq_only`, `ambiguous`,
  `no_resource_id`.
- **Reason:** This is the whole reason the PoC exists. Naive joiners use
  `vms.name = FOCUS.ResourceName` which silently produces zero matches.
- **GOTCHAS:** J-1, J-2, J-3, J-6.

### B-3. Configure the ManageIQ providers and **run a refresh**
- **Done when:** AWS provider has valid creds (rotate `admin:smartvm`
  first &mdash; G-1) and `/api/providers/:id` `refresh` returns
  `success: true`. Azure + OCI providers are decided per B-0.5.
- **Reason:** Adding a provider is not the same as refreshing one (G-4).
  Refresh is **async** &mdash; poll `/api/tasks/:id`. Don't assume
  synchronous success.
- **GOTCHAS:** G-1, G-2, G-3, G-4, G-7, G-8, G-9.

---

## Day 2 — the cost views

### B-4. Wire **the Azure → FOCUS mapping** first (it's the hardest)
- **Done when:** Azure cost-export CSV (PascalCase columns, MeterCategory,
  ARM-path ResourceId, JSON-string Tags column) lands in `focus_costs`
  with v1.3-compliant column names (no deprecated `Provider` /
  `Publisher` &mdash; F-1) and a closed-set `ServiceCategory` (F-2).
- **Reason:** Per SPEC §2, lead with Azure because it's ENBD's real
  near-term pain. The 'AI + Machine Learning' → 'AI and Machine
  Learning' mapping difference is a real bug source.
- **GOTCHAS:** F-1, F-2, J-1.

### B-5. AWS CUR + OCI usage report normalizers
- **Done when:** All three providers' cost data lives in `focus_costs`
  with consistent column shapes. Bedrock per-model SKUs are preserved in
  `sku_meter` for view 1.
- **Reason:** AWS is the "easy" one (CUR is well documented); OCI's free-
  text `product/Description` is unusable for service-category mapping &mdash;
  use `product/sku` instead.
- **GOTCHAS:** F-1, F-2.

### B-6. The on-prem cost recharge module
- **Done when:** `miq_onprem_cost` is populated from either (a) the MIQ
  chargeback module's calculated output (preferred when ENBD already has
  rates configured), or (b) the per-resource formula stub if not.
  Business-unit attribution is via tags or `sub_account_id`.
- **Reason:** The PoC ships the stub (slice 6); the EBA team replaces it
  with real rates on day 2. Don't try to port ManageIQ's chargeback Ruby
  to Python; either read its output (G-5) or own the model in Python
  cleanly (O-1).
- **GOTCHAS:** G-5, O-1, O-2 (the **"don't call this depreciation"** one).

### B-7. The four views with **honest data-source banners**
- **Done when:** `/`, `/views/ai`, `/views/utilization`,
  `/views/cloud-vs-onprem`, `/views/carbon` all render, each one stating
  where its data comes from and what FOCUS can / can't do.
- **Reason:** **This is the answer to leadership's question.** A wrong
  number with no banner is worse than no number with a banner.
- **GOTCHAS:** W-1 (the Starlette TemplateResponse signature flip).

---

## Day 3 — AI, demo, handoff

### B-8. Bedrock NL-query (optional, isolated)
- **Done when:** Canned queries work. Free-text is **off by default**
  (`BEDROCK_DISABLED=1`). The SQL guard rejects every non-SELECT and
  every non-allowlisted table. The me-central-1 residency posture is
  surfaced in the UI banner, not hidden.
- **Reason:** Wrong-cost-with-confident-AI-narration is the worst
  outcome for a bank (SPEC §0). Fail closed.
- **GOTCHAS:** B-1, B-2, B-3, B-4.

### B-9. Carbon: implement nothing, hand over the roadmap
- **Done when:** Leadership has `docs/carbon-roadmap.md` from this PoC,
  with explicit "this is the work the EBA team did NOT do" framing.
- **Reason:** Trying to ship a carbon number in 3 days produces a
  confidently-wrong number. The roadmap is the honest deliverable.

### B-10. **Live demo dress rehearsal**, twice, on the real demo box
- **Done when:** The full `generators → normalizer → join → loader →
  web` pipeline runs end to end in front of a tougher reviewer than
  Sean. Memory headroom is confirmed; the appliance does not OOM.
- **Reason:** Don't do the first dress rehearsal in front of leadership.

---

## What we DELIBERATELY left out (do not let scope creep eat the sprint)

- CMP / Terraform remediation actions &mdash; this is not the engagement scope.
- Real ENBD data &mdash; the synthetic data is the demo. Asking for real data
  pulls legal+security review into the sprint and kills it.
- Real cloud credentials &mdash; same reason.
- A 4-year burndown / depreciation calc &mdash; rename it "monthly recharge"
  (O-2) and move on.
- Carbon numbers &mdash; ship the roadmap; do not invent the data.
- Production auth &mdash; this is a demo. Local-only.

---

## Hand-off artifacts at end of sprint

- `GOTCHAS.md` (25+ entries) &mdash; the **single most valuable thing this
  collaboration produced**. Read first by every new team member.
- `SPEC.md` &mdash; the design that was approved up front.
- This file (`EBA-BACKLOG.md`) &mdash; what the team built and what they did not.
- `docs/carbon-roadmap.md` &mdash; the honest answer to req #4.
- Running app on docker-compose &mdash; the demo surface.

The deliverable for AWS is the **first** of those four. The running app is
disposable.
