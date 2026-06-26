# focus-manageiq-bridge

**Bridges multi-cloud FOCUS cost data to ManageIQ inventory + utilization.**
Built for the Emirates NBD "Multi-Cloud Cost Optimization" engagement.
Ingests native **FOCUS 1.2** exports from AWS / Azure / OCI (or, for
historical data, parses CUR / cost-export / usage-report formats), joins
them to **ManageIQ** inventory and utilization on a per-provider asymmetric
key, and serves leadership views with honest data-source verdicts.

The hard, valuable thing this does — and what the name reflects — is the
**FOCUS ↔ ManageIQ join** (the cost data carries no utilization or on-prem
identity; ManageIQ has both but no FOCUS cost). Everything else is a view
over that bridge.

> Git directory is still `enbd-multicloud-finops-poc`; rename to
> `focus-manageiq-bridge` at a clean checkpoint (it changes the auto-memory
> path + breaks the running container/venv mid-session, so it's a deliberate
> step, not done live).

> **All data is synthetic.** `DEMO-` prefixes, fake account IDs, no real
> cloud credentials. The most valuable artifact in this repo is
> [`GOTCHAS.md`](GOTCHAS.md) — every non-obvious issue hit during the build,
> framed for the EBA sprint team. See [`SPEC.md`](SPEC.md) for the design and
> [`docs/production-architecture.md`](docs/production-architecture.md) for the
> production target.

## Run it (one command)

```bash
docker compose up --build
```

Then open **http://localhost:8000**. The stack:

1. starts Postgres (`db`),
2. applies the schema,
3. seeds synthetic data end-to-end
   (generators → connector dispatcher → FOCUS → join → load → on-prem),
4. serves the dashboard (gunicorn + uvicorn workers).

Everything is synthetic and the stack is self-contained — runs on a laptop,
on-prem, or any AWS host (the SPEC §2 portability invariant).

## The five views

| Path | Requirement | FOCUS verdict |
|------|-------------|---------------|
| `/` | Overview + verdict ledger + join distribution | — |
| `/views/ai` | AI cost by cloud & model (Bedrock, Azure OpenAI, OCI gen-AI) | ✅ native |
| `/views/utilization` | Utilization × cost (rightsizing) | ⚠️ partial — util not in FOCUS |
| `/views/cloud-vs-onprem` | Cloud vs on-prem cost | ⚠️ conditional — FOCUS doesn't source on-prem |
| `/views/carbon` | Carbon footprint | ❌ out of FOCUS — roadmap, not a number |
| `/ai/` | Optional Bedrock NL-query (off by default, fail-closed) | — |
| `/connect/` | **Connect a data source** — register, dispatch, see rows appear | — |

## Connect-and-run

The headline: onboarding a cloud account is a **registry row + a credential
reference, never a code change**. The `/connect/` page registers a source and
re-runs the dispatcher; the views update. In this PoC a source reads a local
synthetic export; production swaps `discover()` for S3/blob listing and
resolves the credential from Secrets Manager — the FOCUS-mapping core is
unchanged. See `connectors/` and `docs/production-architecture.md` §0a.

## Local dev (without Docker)

```bash
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
# bring up a Postgres however you like, point FOCUS_PG_* at it
python -m docker.seed            # generators → dispatcher → join → load → onprem
.venv/bin/python -m uvicorn web.app:app --port 8000
```

## Layout

```
generators/   synthetic provider-native cost exports (AWS/Azure/OCI) + MIQ snapshot
connectors/   connect-and-run: registry, adapters, dispatcher, /connect router
normalizer/   provider-native → FOCUS v1.3 (lead: Azure)
join/         FOCUS ↔ ManageIQ resource_join_map (the hard part — asymmetric keys)
db/           Postgres schema + loader (dual-mode: docker exec | network psql)
onprem/       on-prem recharge cost model
web/          FastAPI + Jinja2 dashboard (Dark Precision SaaS UI)
ai/           optional Bedrock NL-query, SQL-guardrailed, fail-closed
docker/       seed pipeline + container entrypoint
docs/         production architecture, carbon roadmap, archived spec
GOTCHAS.md    THE deliverable — every non-obvious issue, for the EBA team
EBA-BACKLOG.md what ENBD builds during the 3-day sprint, in order
```
