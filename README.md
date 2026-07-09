# focus-manageiq-bridge

**Bridges multi-cloud FOCUS cost data to ManageIQ inventory + utilization.**
Built ahead of the AnyBank "Multi-Cloud Cost Optimization" EBA sprint,
as a de-risking proof-of-concept the AnyBank team inherits and builds on.

> **All data is synthetic.** `DEMO-` prefixes, fake account IDs, no real
> cloud credentials, no real AnyBank data — by design (SPEC §0).

It ingests native **FOCUS** exports from AWS / Azure / OCI (or, for
historical data, parses CUR / cost-export / usage-report formats),
normalizes them to a **FOCUS v1.3** warehouse, joins them to **ManageIQ**
inventory and utilization on a per-provider asymmetric key, and serves
leadership views with honest data-source verdicts.

The hard, valuable thing this does — and what the name reflects — is the
**FOCUS ↔ ManageIQ join**: the cost data carries no utilization or on-prem
identity; ManageIQ has both but no FOCUS cost. Everything else is a view
over that bridge.

## Start here

| Read | Why |
|------|-----|
| [`GOTCHAS.md`](GOTCHAS.md) | **The primary deliverable.** 100+ non-obvious findings from the build — data-mapping traps, the join landmines, residency friction — each with a "what / why it matters / action" for the sprint team. |
| [`EBA-BACKLOG.md`](EBA-BACKLOG.md) | What the team builds during the 3-day sprint, in order, with GOTCHAS citations. |
| [`SPEC.md`](SPEC.md) | The approved design this was built to. |
| [`docs/production-architecture.md`](docs/production-architecture.md) | The production target (ROSA + Aurora + S3 landing, region-parameterized) and what survives from the PoC. |

## Run it (two commands)

```bash
cp .env.example .env   # sets the local demo DB password — no baked-in default
docker compose up --build
```

There is deliberately **no built-in database password**: the stack fails fast
with a clear message if `FOCUS_PG_PASS` is unset, so a copied deployment can
never silently run on a known credential. The demo value lives only in
`.env.example`.

Then open **http://localhost:8000**. The stack:

1. starts Postgres (`db`),
2. applies the schema,
3. seeds synthetic data end-to-end
   (generators → connector dispatcher → FOCUS → join → load → on-prem),
4. serves the dashboard (gunicorn + uvicorn workers).

Everything is synthetic and the stack is self-contained — runs on a laptop,
on-prem, or any AWS host (the SPEC §2 portability invariant). Base images
pull from ECR Public (no Docker Hub account or rate limits).

## The views

| Path | Requirement | FOCUS verdict |
|------|-------------|---------------|
| `/` | Overview + verdict ledger + join distribution | — |
| `/views/ai` | AI cost by cloud & model (Bedrock, Azure OpenAI, OCI gen-AI) | ✅ native |
| `/views/utilization` | Utilization × cost (rightsizing) | ⚠️ partial — util not in FOCUS |
| `/views/cloud-vs-onprem` | Cloud vs on-prem cost | ⚠️ conditional — FOCUS doesn't source on-prem |
| `/views/carbon` | Carbon footprint | ❌ out of FOCUS — roadmap, not a number |
| `/ai/` | Optional Bedrock NL-query (off by default, fail-closed) | — |
| `/connect/` | **Connect a data source** — register, dispatch, see rows appear | — |

Every view carries a data-source banner stating where its numbers come from
and what FOCUS can and cannot answer — a wrong-but-confident number is the
worst outcome for a bank, so the UI never fakes coverage it doesn't have.

## Connect-and-run

Onboarding a cloud account is a **registry row + a credential reference,
never a code change**. The `/connect/` page registers a source and re-runs
the dispatcher; the views update. In this PoC a source reads a local
synthetic export; production swaps `discover()` for S3/blob listing and
resolves the credential from Secrets Manager — the FOCUS-mapping core is
unchanged. See `connectors/` and `docs/production-architecture.md` §0a.

## The optional AI layer

Free-text natural-language query runs on Amazon Bedrock and is **isolated
and fail-closed**: the whole console works with the AI container stopped,
canned queries run unconditionally, and every generated query must pass a
parser-level SQL allowlist (`ai/sql_guard.py`) — a single SELECT against
four allowlisted tables, or it's rejected. Every answer shows the SQL it
ran and the rows it got.

## Local dev (without Docker)

```bash
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
# bring up a Postgres however you like, point FOCUS_PG_* at it
python -m docker.seed            # generators → dispatcher → join → load → onprem
.venv/bin/python -m uvicorn web.app:app --port 8000
```

Tests (pure-logic suite always runs; DB-backed integrity tests skip cleanly
without a seeded Postgres):

```bash
FOCUS_PG_HOST=127.0.0.1 FOCUS_PG_PASS=<your db password> \
  .venv/bin/python -m pytest tests/ -q
```

## Layout

```
generators/   synthetic provider-native cost exports (AWS/Azure/OCI) + MIQ snapshot
connectors/   connect-and-run: registry, adapters, dispatcher, /connect router
normalizer/   provider-native → FOCUS v1.3 (lead: Azure)
join/         FOCUS ↔ ManageIQ resource_join_map (the hard part — asymmetric keys)
db/           Postgres schema + loader (dual-mode: docker exec | network psql)
onprem/       on-prem recharge cost model
web/          FastAPI + Jinja2 dashboard
ai/           optional Bedrock NL-query, SQL-guardrailed, fail-closed
docker/       seed pipeline + container entrypoint
deploy/       terraform (Aurora/VPC), OpenShift helm chart, CloudFront front door
docs/         production architecture, carbon roadmap, archived spec
tests/        SQL-guard logic tests + DB-backed integrity tests (CI-gated)
GOTCHAS.md    THE deliverable — every non-obvious issue, for the EBA team
EBA-BACKLOG.md what AnyBank builds during the 3-day sprint, in order
```

## Status

This is a proof-of-concept, not a product: no production auth or
multi-tenancy, synthetic data only, and the security posture documented in
`docs/production-architecture.md` §4 is the production plan, not the
current state. Code here that survives to production (normalizer, schema,
loader, join) still requires the customer's own security review first.
