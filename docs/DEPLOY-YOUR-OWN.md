# Deploy your own FinOps console

This product is **config-driven and single-tenant per deploy**: each customer
runs their own instance. Onboarding is *configuration*, not a code fork. There
are exactly two things to configure — branding/currency, and your data sources.

## 1. Clone

```bash
git clone <this-repo> my-finops && cd my-finops
```

## 2. Configure your tenant (branding + reporting currency)

Copy the example and edit it:

```bash
cp config/tenant.example.json config/tenant.json
$EDITOR config/tenant.json
```

`config/tenant.json` is the ONLY file you edit to rebrand and set your
reporting currency. Fields:

| Field | Meaning |
|-------|---------|
| `org_name` | Your organization (shown in the title bar, sidebar, user chip) |
| `product_name` | The console name (first word becomes the brand mark) |
| `user_label` / `user_initials` | The signed-in user chip (initials derived if omitted) |
| `environment_note` | Honesty banner text (keep it accurate — it states what the data is) |
| `reporting_currency` | The single currency the dashboard sums in (e.g. `USD`, `EUR`) |
| `fx_to_reporting` | FX rate from each source currency INTO `reporting_currency`; the reporting currency itself is forced to `1.0` |

A few fields can also be set by environment variable for a deploy pipeline that
doesn't bake a file: `TENANT_ORG_NAME`, `TENANT_PRODUCT_NAME`,
`TENANT_USER_LABEL`, `TENANT_REPORTING_CURRENCY` (env wins over the file).

> **Currency integrity:** `fx_to_reporting` must list every currency your
> sources bill in. An unknown currency raises rather than silently mis-summing
> (the B-6/B-7 rule). Never SUM mixed currencies — the platform converts to the
> reporting currency before any aggregate.

## 3. Bring up the stack

```bash
docker compose up --build
```

This starts Postgres + the web console on `http://localhost:8000` and seeds
**synthetic** data so the dashboard is populated on first run. The synthetic
data is obviously fake (`DEMO-` prefixes, fake account IDs) — it is a working
demo, not your real spend.

## 4. Load your own data

Your real cost data never requires a code change. Two paths:

- **Upload** (available now): on **Connect a data source → Upload a FOCUS
  export**, upload a provider FOCUS export CSV (AWS / Azure / OCI). It is
  validated as FOCUS-conformant, normalized, loaded, and joined — replacing
  only that source's partition (incremental; the rest of your data is
  untouched). This is the production ingestion path, exercised on synthetic
  data in the demo.
- **API-pull** (later release): connectors that pull exports directly from your
  cloud accounts plug in behind the same `SourceAdapter` contract. They are
  shown disabled until shipped. To write your own connector, see
  [`connectors/README.md`](../connectors/README.md).

## 5. Operate

- **Health:** `GET /healthz` (unauthenticated; for load-balancer probes).
- **Metrics:** `GET /metrics` (Prometheus text; unauthenticated, expose only
  inside your cluster — see OBS-1 in `GOTCHAS.md`).
- **Audit:** state-changing actions (upload / add / remove a source) emit an
  `event":"audit"` JSON log line with a request id — route these to a retained
  store for compliance.
- **Auth:** set `BASIC_AUTH_USER` and `BASIC_AUTH_PASS` to gate the console
  (off by default for local dev). For internet exposure, also front it per
  `deploy/cloudfront/README.md` (CX-6).

## What stays the same across tenants

The hard, valuable parts are shared and need no per-tenant change: the FOCUS
normalization, the FOCUS↔ManageIQ identity join, the conformance validator, the
incremental per-source load, and the connector SDK. That's the point of
config-driven single-tenant — the engine is the product; the config is the
deployment.

## What is NOT in scope (by design)

Multi-tenant SaaS, SSO, per-tenant data isolation in one shared instance. If you
need to serve many orgs, run many instances — one config + one deploy each.
