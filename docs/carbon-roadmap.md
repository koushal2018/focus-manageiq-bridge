# Carbon footprint roadmap

**Status:** ENBD multi-cloud FinOps PoC, slice 8.
**Date frozen:** 2026-06-26.
**Audience:** ENBD engineering + finance leadership reviewing requirement #4
("Carbon footprint" in the original brief).

---

## TL;DR

**FOCUS cannot answer this requirement.** Through v1.4, the FOCUS specification
does not define a carbon, emissions, or energy column. Any carbon number this
PoC could show derived from FOCUS data alone would be confidently wrong &mdash;
exactly the failure mode SPEC §0 warns the customer against.

The honest path forward has four data streams, each with its own gotchas. They
combine into a per-business-unit kgCO<sub>2</sub>e column, but the EBA team
must build the assembly themselves; no single vendor ships it.

---

## Data streams by provider

### AWS &mdash; **Customer Carbon Footprint Tool (CCFT)**

- **What:** Built-in AWS Console feature, free. Reports Scope 2 (electricity)
  emissions per AWS account, per service, per region, in **kgCO<sub>2</sub>e**
  with **three-month lag**.
- **Format:** Console-only initially; **CCFT export to S3 became available in
  2024** (`carbonemission`-prefixed exports landing in your billing S3
  bucket).
- **Granularity:** Monthly. Per AWS service per region. Not per resource.
- **Reach:** Scope 2 only. Scope 1 (direct emissions) and Scope 3 (supply chain)
  are not in CCFT. AWS publishes its own Scope 3 in its annual sustainability
  report.
- **Where it falls short for ENBD:** Per-account is too coarse for chargeback
  by business unit unless ENBD already splits BUs into separate AWS accounts
  (and the CCFT mapping then mirrors that split exactly).
- **EBA wiring sketch:**
  - Enable CCFT S3 export on the payer account.
  - Land the monthly carbon CSV alongside the CUR in the same bucket.
  - Join `carbon_per_service` to the PoC's `focus_costs` on
    `(BillingAccountId, ServiceName, BillingPeriodStart)` &mdash; an
    intentionally loose join because CCFT's "service name" is closer to FOCUS
    `ServiceName` than to `ResourceId`.

### Azure &mdash; **Emissions Impact Dashboard** (formerly Sustainability Calculator)

- **What:** Power BI dashboard published by Microsoft, free. Per-subscription
  emissions in metric tons CO<sub>2</sub>e, monthly, with similar lag.
- **Format:** **Power BI export to Excel/CSV is supported**, no programmatic
  API at time of writing (2026-06). For automation, ENBD must shim through
  the Power BI REST API.
- **Granularity:** Per Azure subscription, per service category, monthly.
- **Reach:** Scope 1, 2, **and partial Scope 3** (a meaningful differentiator
  vs. AWS CCFT).
- **EBA wiring sketch:** Schedule a weekly Power BI dataflow export to an
  Azure Storage Account; ingest into the FinOps Postgres on the same cadence
  as the cost data.

### OCI &mdash; **no first-party feed**

- **What:** Oracle does not, at time of writing, publish a per-tenancy carbon
  feed equivalent to CCFT or Emissions Impact Dashboard. There is an
  Oracle-wide sustainability report and "carbon-neutral cloud" marketing, but
  no per-account / per-resource carbon data exposed to customers.
- **Workaround:** Use the **Cloud Carbon Footprint** open-source project
  (cloudcarbonfootprint.org) which estimates OCI carbon from billing-driven
  usage + published regional grid intensities. Estimate, not measurement.
- **EBA wiring sketch:** Run CCF as a sidecar against the OCI usage report;
  store the output in a `carbon_estimates_oci` table flagged as estimated, not
  measured. **The data-source banner in the web layer MUST surface this distinction.**

### On-prem (ENBD data centers) &mdash; **custom model**

- **What:** ENBD owns the data centers, so ENBD also owns the model. There is
  no vendor to ask.
- **Inputs needed:**
  - **kWh per VM** &mdash; ManageIQ does not measure power directly. Derived
    from CPU utilization × server TDP (thermal design power) + a constant for
    memory + storage.
  - **Power Usage Effectiveness (PUE)** &mdash; per ENBD DC. Multiplies IT
    power by ~1.3–1.5 to include cooling overhead.
  - **Grid carbon intensity** &mdash; UAE grid: **published by the IEA at
    roughly 0.45 kgCO<sub>2</sub>e per kWh** (verify against the latest
    public figure; this carries multi-percent year-on-year movement).
- **Formula:** `kgCO2e = (vm_cpu_avg × server_TDP_W + RAM_W + storage_W) × hours × PUE × grid_intensity_kg_per_kWh / 1000`.
- **EBA wiring sketch:** Add a `dc_assumptions` table (one row per DC) and
  compute on-prem carbon as a derived view next to `miq_onprem_cost`. The
  result lands in the same `kgCO2e` column shape as the cloud streams so the
  reporting view can `UNION ALL` cleanly.

---

## What this PoC delivers (slice 8)

- `/views/carbon` &mdash; a stub view that explicitly states "**FAKE**" on the
  numbers and lists the four feeds above with their realistic
  status.
- This document (`docs/carbon-roadmap.md`) as the deliverable the EBA team
  hands to leadership when they say "we did not build a carbon number; we
  built a roadmap to a defensible one."

---

## What this PoC does **not** deliver

- Any real carbon number. Period.
- A scope-1/2/3 mapping. That is a finance-and-sustainability decision, not
  an engineering one.
- An offset/credit reconciliation.

---

## Gotchas, captured

The following from `GOTCHAS.md` are load-bearing for this slice:

- **No FOCUS carbon column** &mdash; would be in a new `Carbon` namespace if
  FOCUS adopts the FinOps Foundation's Carbon Working Group output; track
  that conversation, do not build assuming it lands.
- **AWS CCFT 3-month lag** &mdash; the demo's "current carbon" tile is, at
  best, "three months ago's carbon." Be explicit on the dashboard.
- **The Azure EID export is manual today** &mdash; Power BI REST scrape is
  the only automation path. Plan for it to break when Microsoft refactors
  the dashboard. Wire it through a feature flag.
- **The on-prem PUE is a load-bearing assumption** &mdash; the difference
  between PUE 1.3 and PUE 1.6 is ~25% of the on-prem carbon total. Get the
  DC owner to sign off on the value in writing before any number appears in
  a leadership deck.

---

## Bottom line

Carbon is **honestly an "out of scope for FOCUS"** answer to leadership. The
work the EBA team needs to do is not "extend FOCUS to carry carbon" &mdash; that
breaks the spec &mdash; but **stand up the four streams above next to FOCUS** and
join at the view layer, the same way utilization is joined.

The PoC's view 4 layout is ready for those numbers when the EBA team has them.
