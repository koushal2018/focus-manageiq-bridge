# focus-manageiq-bridge — Claude session brief

**Read `SPEC.md` for the full design.** It is the source of truth and was approved before any code was written. This file only restates the non-negotiables and points at the things easy to forget.

## What this is
A throwaway de-risking spike for the AnyBank "Multi-Cloud Cost Optimization" engagement, built ahead of a 3-day **EBA innovation sprint** so the AnyBank team can build the real thing themselves, faster. **The deliverable is `GOTCHAS.md` more than the running app.**

## Non-negotiables (will trip a future session)
- **Messy synthetic data, not clean.** Clean data hides the FOCUS↔ManageIQ join problem, which is the whole point. See `SPEC.md` §3.1.
- **Build by risk, not by feature order.** Hardest first: the join, then the Azure→FOCUS mapping. AI cost view is easiest and last-but-one. See `SPEC.md` §2 build order.
- **No real customer data, no real cloud creds, no production posture.** Synthetic data must be obviously fake (`DEMO-` prefixes, fake account IDs).
- **Honest data-source banners on every UI view.** Each view states where its data comes from and what FOCUS can/can't do. Carbon is a stub; show it as one.
- **Bedrock layer is optional and isolated.** FOCUS must work fully with the AI container stopped. Canned/parameterized queries before free-text. Wrong-cost-with-confident-narration is the worst outcome for a bank.
- **Bedrock residency:** me-central-1 is Global-inference-profile only — document as a gotcha, don't paper over it.
- **Portability invariant:** docker-compose, runs on-prem OR AWS. Hosting decision is open.

## Out of scope (don't drift into these)
CMP/Terraform remediation, real creds, real customer data, production auth/multi-tenancy, real carbon feeds, the 4-year burndown calc.

## Repo layout (planned per SPEC §3, not yet built)
```
generators/    synthetic provider-native cost exports (AWS/Azure/OCI) + MIQ seed loader
normalizer/    provider-native → FOCUS, lead with Azure mapping
miq_collector/ talks to real ManageIQ REST API
db/            postgres schema (focus_costs, miq_utilization, miq_onprem_cost, resource_join_map)
web/           CMP-style single page, four views with data-source banners
ai/            Bedrock NL-query (FastAPI), built last, optional
GOTCHAS.md     THE deliverable — every non-obvious issue, framed for the EBA team
EBA-BACKLOG.md short ordered list of what AnyBank builds during the sprint
```

## Skills loaded for this project
- `manageiq-sme` — user-level at `~/.claude/skills/manageiq-sme/`. Originally written for a sibling project (`enbd_manageiq`) so its project-specific paths (e.g. `ai/manageiq_client.py`, `focus_v1_2_view`) DO NOT apply here. The ManageIQ API/VMDB knowledge does.
- Use the `/frontend-design` skill when building UI components.
- **`/reseed`** (project, user-only) — rebuild web image → re-seed synthetic data → pytest → conformance → route smoke. Run after any generator/normalizer/join/loader/template change.

## Tests & CI
- **`tests/`** — `test_sql_guard.py` (pure logic, always runs) + `test_data_integrity.py` (DB-backed, skips if no Postgres). Guards the B-6/B-7 currency/join bugs + the SQL allowlist. Run: `FOCUS_PG_HOST=127.0.0.1 FOCUS_PG_PASS=focus_app_demo .venv/bin/python -m pytest tests/ -q`.
- **`.github/workflows/ci.yml`** — py_compile + pytest with a real Postgres service (seeds, then runs the integrity tests) on every push/PR. AI layer off in CI.
- pytest (and the httpx test client) are dev/CI only — in `requirements-dev.txt`, deliberately NOT in the production image (the Dockerfile installs only `requirements.txt`; non-root `/opt/venv` is read-only). CI installs both: `pip install -r requirements.txt -r requirements-dev.txt`.

## MCP servers (declared in `.mcp.json`)
- **`focus-finops`** (HTTP) — authoritative FOCUS column/spec lookup. Use it before quoting FOCUS rules from memory.
- **`postgres`** (stdio, `uvx postgres-mcp --access-mode restricted`) — read-only query/introspection of the local `focus` DB. Requires the compose stack up (the db now publishes `127.0.0.1:5432`) AND `FOCUS_PG_PASS` exported at session start — the DSN has **no default password** (SEC-7), so without the env var the server can't connect. Localhost + synthetic only — never point at real customer data.
- **`context7`** (stdio, `npx @upstash/context7-mcp`) — up-to-date library/framework docs (FastAPI, boto3, etc.). Verified it starts.
- **`memory`** (stdio, `npx @modelcontextprotocol/server-memory`) — generic cross-session memory. Note: GOTCHAS.md + file auto-memory remain the project's primary, deliverable memory; this is supplementary.
- **`github`** (stdio, `npx @modelcontextprotocol/server-github`) — **inert until you set `GITHUB_PERSONAL_ACCESS_TOKEN`** in the environment (no token is committed; the config references the env var). `gh` CLI is not installed.
- AWS MCP is **already available via the `aws-core` plugin** (`mcp__plugin_aws-core_aws-mcp__*`) — do NOT add a second; it would duplicate.
- All `npx`/`uvx` servers register at **session start** and need Node (installed) / uv. Adding to `.mcp.json` does not load them mid-session.

## Hooks (in `.claude/settings.json`; scripts in `.claude/hooks/`)
- **`PostToolUse` on Edit|Write** → `python3 -m py_compile` on `*.py`. Catches syntax errors at write-time.
- **`PostToolUse` on Edit|Write** → `currency-tripwire.sh`: warns (never blocks) when a `*.py` aggregates raw `billed_cost` without `_usd` — the B-6/B-7 bug class.
- **`PreToolUse` on Bash (git commit/push)** → `secret-guard.sh`: scans staged diff for credential patterns; **blocks (exit 2)** on a hit. Prevents a CX-6/G-1 secret leak.
- **`Stop`** → `git-work-at-risk.sh`: surfaces uncommitted files + unpushed commits (ENV-1) as a `systemMessage`.
- **`UserPromptSubmit`** → injects a per-turn reminder to capture non-obvious findings in `GOTCHAS.md`.
- **`SessionStart`** → echoes `tail -30 GOTCHAS.md` when it exists, so the running gotcha list is always in context.

## Permission rules (project scope)
- **Ask** before `git push`, `git push --force`, `git reset --hard`, `git branch -D`, `git clean -f`, `git checkout --`.
- **Deny** edits/writes to `SPEC.md` (design-approved; revise the archive in `docs/superpowers/specs/` instead) and any `rm -rf /` or `rm -rf ~/`.

## Working agreements
- Confirm before destructive or shared-state actions (git push, force-push, branch delete, merging to main).
- Evidence before "done" — run the verifier; don't claim success from type-check alone.
- Terse responses; the diff and the file are the artifact.
