---
name: reseed
description: Rebuild the web image, re-seed the synthetic FOCUS data, then verify — reconciliation + route smoke. Use after changing generators, normalizer, connectors, join, the loader, or any template/route, to refresh the running stack and confirm it's still correct.
disable-model-invocation: true
---

# Reseed & verify the running stack

A side-effecting maintenance loop — **user-invoked only** (`/reseed`). It
truncates and reloads the synthetic data, so never run it expecting the DB to
be preserved.

Run these steps in order from the project root. Stop and report if any step
fails; do not claim success without the evidence from steps 4–5.

## 1. Rebuild the web image (templates + code are baked in — W-3)
```bash
docker compose build web && docker compose up -d web
```
Wait ~5s for the container to be healthy (`docker compose ps`).

## 2. Re-seed the synthetic data (idempotent: truncates + reloads)
```bash
docker compose exec -T -e FOCUS_PG_HOST=db -e FOCUS_PG_PASS=focus_app_demo web python -m docker.seed
```
Expect: `[seed] complete` with focus_costs / resource_join_map / miq_utilization
/ miq_onprem_cost row counts.

## 3. Run the test suite against the freshly seeded DB
```bash
FOCUS_PG_HOST=127.0.0.1 FOCUS_PG_PASS=focus_app_demo .venv/bin/python -m pytest tests/ -q
```
Expect: all tests pass (the data-integrity tests catch B-6/B-7 currency/join
regressions). The DB port is published to 127.0.0.1 (see docker-compose.yml).

## 4. Reconciliation check (the B-7 guard, explicit)
```bash
docker compose exec -T -e FOCUS_PG_HOST=db -e FOCUS_PG_PASS=focus_app_demo web python -c "
from web import queries as q
c=q.focus_conformance()
print('conformance:', c['rules_passed'],'/',c['rules_total'], 'conformant=',c['conformant'])
"
```
Expect: all rules pass, `conformant= True`.

## 5. Route smoke (every page must answer)
```bash
docker compose exec -T web python -c "
import urllib.request,urllib.error
def st(p):
  try: return urllib.request.urlopen('http://localhost:8000'+p,timeout=8).status
  except urllib.error.HTTPError as e: return e.code
routes=['/','/login','/welcome','/views/ai','/views/utilization','/views/cloud-vs-onprem','/views/carbon','/workload/90010','/connect/','/ai/','/faq','/healthz']
r={p:st(p) for p in routes}
ok=all(v==200 for v in r.values())
print('ALL 200:', ok, r)
"
```
Expect: `ALL 200: True`.

## Report
Summarize: row counts (step 2), test result (step 3), conformance (step 4),
routes green (step 5). If the UI changed and it's a customer-facing surface,
note that a visual check (screenshot or manual) is still worth doing — route
200 ≠ renders correctly (W-12).
