# enbd-multicloud-finops-poc — Claude session brief

**Read `SPEC.md` for the full design.** It is the source of truth and was approved before any code was written. This file only restates the non-negotiables and points at the things easy to forget.

## What this is
A throwaway de-risking spike for the ENBD "Multi-Cloud Cost Optimization" engagement, built ahead of a 3-day **EBA innovation sprint** so the ENBD team can build the real thing themselves, faster. **The deliverable is `GOTCHAS.md` more than the running app.**

## Non-negotiables (will trip a future session)
- **Messy synthetic data, not clean.** Clean data hides the FOCUS↔ManageIQ join problem, which is the whole point. See `SPEC.md` §3.1.
- **Build by risk, not by feature order.** Hardest first: the join, then the Azure→FOCUS mapping. AI cost view is easiest and last-but-one. See `SPEC.md` §2 build order.
- **No real ENBD data, no real cloud creds, no production posture.** Synthetic data must be obviously fake (`DEMO-` prefixes, fake account IDs).
- **Honest data-source banners on every UI view.** Each view states where its data comes from and what FOCUS can/can't do. Carbon is a stub; show it as one.
- **Bedrock layer is optional and isolated.** FOCUS must work fully with the AI container stopped. Canned/parameterized queries before free-text. Wrong-cost-with-confident-narration is the worst outcome for a bank.
- **Bedrock residency:** me-central-1 is Global-inference-profile only — document as a gotcha, don't paper over it.
- **Portability invariant:** docker-compose, runs on-prem OR AWS. Hosting decision is open.

## Out of scope (don't drift into these)
CMP/Terraform remediation, real creds, real ENBD data, production auth/multi-tenancy, real carbon feeds, the 4-year burndown calc.

## Repo layout (planned per SPEC §3, not yet built)
```
generators/    synthetic provider-native cost exports (AWS/Azure/OCI) + MIQ seed loader
normalizer/    provider-native → FOCUS, lead with Azure mapping
miq_collector/ talks to real ManageIQ REST API
db/            postgres schema (focus_costs, miq_utilization, miq_onprem_cost, resource_join_map)
web/           CMP-style single page, four views with data-source banners
ai/            Bedrock NL-query (FastAPI), built last, optional
GOTCHAS.md     THE deliverable — every non-obvious issue, framed for the EBA team
EBA-BACKLOG.md short ordered list of what ENBD builds during the sprint
```

## Skills loaded for this project
- `manageiq-sme` — user-level at `~/.claude/skills/manageiq-sme/`. Originally written for a sibling project (`enbd_manageiq`) so its project-specific paths (e.g. `ai/manageiq_client.py`, `focus_v1_2_view`) DO NOT apply here. The ManageIQ API/VMDB knowledge does.

## MCP servers (declared in `.mcp.json`)
- **`focus-finops`** (HTTP) — authoritative FOCUS column/spec lookup. Use it before quoting FOCUS rules from memory.
- **Postgres MCP — DEFERRED.** Install when the docker-compose stack actually has Postgres running: `claude mcp add postgres -- uvx postgres-mcp postgres://focus:focus@localhost:5432/focus`. Pointed at `localhost` only — never at real ENBD data.

## Hooks (in `.claude/settings.json`)
- **`PostToolUse` on Edit|Write** → `python3 -m py_compile` on `*.py`. Catches syntax errors at write-time.
- **`UserPromptSubmit`** → injects a per-turn reminder to capture non-obvious findings in `GOTCHAS.md`.
- **`SessionStart`** → echoes `tail -30 GOTCHAS.md` when it exists, so the running gotcha list is always in context.

## Permission rules (project scope)
- **Ask** before `git push`, `git push --force`, `git reset --hard`, `git branch -D`, `git clean -f`, `git checkout --`.
- **Deny** edits/writes to `SPEC.md` (design-approved; revise the archive in `docs/superpowers/specs/` instead) and any `rm -rf /` or `rm -rf ~/`.

## Working agreements
- Confirm before destructive or shared-state actions (git push, force-push, branch delete, merging to main).
- Evidence before "done" — run the verifier; don't claim success from type-check alone.
- Terse responses; the diff and the file are the artifact.
