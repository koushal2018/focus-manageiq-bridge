# GOTCHAS — ENBD Multi-Cloud FinOps PoC

Running log of every non-obvious thing hit while building this PoC. Framed for the ENBD engineering team who will rebuild this during the 3-day EBA innovation sprint. Each entry: **what we hit**, **why it matters**, **what to do about it**.

> **The PoC's most valuable artifact is this file**, not the code. Append generously; the running app is disposable.

---

## 🚨 LANDMINE — read first

### LM-1. The ManageIQ Quinteros appliance eats memory until the host OOM-kills *everything else*
- **What:** Running `manageiq_appliance` (the official `manageiq/manageiq:quinteros-1` Docker image) on the dev EC2 instance consumed **~8.5 GB of 16 GB RAM within 10 minutes of startup**, with load average climbing past 9. The host's other workloads (VS Code remote server, this Claude Code session) got OOM-killed. The VS Code TUI surfaced this as `The window terminated unexpectedly (reason: 'crashed', code: '5')` — a misleading message that looks like a frontend bug.
- **Why it matters:** Three layered traps:
  1. **The crash signature is uninformative.** Nothing in VS Code's error says "OOM" or "ManageIQ took all your RAM." A team member will blame VS Code, the IDE, or the EC2 instance — not the container they started 10 minutes ago.
  2. **ManageIQ doesn't self-limit.** The image runs the full appliance stack — Postgres, Puma (Rails), evmserverd, multiple Ruby workers, message bus, automation engine — with no memory cap. It will use everything the host gives it.
  3. **`docker restart` on the same image will reproduce the crash.** The container's default restart policy is `unless-stopped`; an `Out of Memory` kill from the kernel causes the daemon to restart it, which starts the OOM cycle again.
- **EBA action:**
  1. **Always run the appliance with `--memory=6g --memory-swap=6g`** (or smaller — try 4g first). The ENBD team's existing appliance presumably has a larger host; on a typical EC2 dev box this is non-negotiable.
  2. **Set the container restart policy to `no`**: `docker update --restart=no manageiq_appliance` after the first run. Without this, an OOM-kill triggers an immediate restart loop.
  3. **Plan compute capacity before the EBA sprint.** A laptop/dev VM at 16 GB cannot host the appliance AND a Postgres container AND the web service AND the Bedrock service together. Minimum realistic memory for the full PoC stack: 24–32 GB, or split the appliance onto its own host.
  4. **If the IDE crashes mid-build with `code: '5'`** — first thing to check is `dmesg | grep -i oom` on the host, not the IDE logs.

### LM-2. ManageIQ container's restart-loop survives `docker stop` and `systemctl disable`
- **What:** After the OOM-crash, the container kept restarting itself. `systemctl disable docker` did not stick across socket-activation — Docker's `docker.socket` unit re-activated the daemon on first client connection. Only `systemctl mask docker.service docker.socket containerd.service` made it permanently stop, and only after `docker rm` removed the container (so the restart policy had no target).
- **Why it matters:** The instinct ("just stop Docker") is insufficient when the daemon is socket-activated *and* the container restart policy is aggressive. A half-disabled Docker silently revives the appliance the next time anything touches `/var/run/docker.sock`.
- **EBA action — full disable sequence:**
  ```bash
  docker update --restart=no manageiq_appliance        # break restart loop
  docker stop manageiq_appliance                       # stop
  docker rm   manageiq_appliance                       # remove (no target to restart)
  sudo systemctl mask docker.service docker.socket containerd.service
  ```
  To bring it back later, **always** include a memory limit on the run command (LM-1).

---

## ManageIQ appliance

### G-1. Default admin credential is `admin:smartvm`
- **What:** The Quinteros appliance ships with HTTP Basic `admin:smartvm` enabled out of the box; this PoC uses it as-is.
- **Why it matters:** Acceptable for a throwaway spike on a synthetic appliance. **Not acceptable for ENBD's real deployment** — must be rotated before any environment that holds real cost data, and the password change has to be persisted through the appliance's `evmserverd` restart, which is non-obvious.
- **EBA action:** First task on day 1 — change the admin password (the ManageIQ docs page `/api/examples/password_update.html` shows the API call). Then update the collector to read the new cred from a secret.

### G-2. ManageIQ `Bearer` token auth is **NOT** standard `Authorization: Bearer <jwt>`
- **What:** ManageIQ's token-based auth is bespoke. You call `POST /api/auth` with HTTP Basic, get back `{"auth_token": "..."}`, then send subsequent requests with the header `X-Auth-Token: <token>` — **NOT** `Authorization: Bearer ...`.
- **Why it matters:** Every model I've ever asked for "ManageIQ auth code" hallucinated `Authorization: Bearer` because that's what 99% of REST APIs use. Confirmed against `/docs/reference/quinteros/api/overview/authentication.html` before writing this. Token lifetime defaults short — re-auth on expiry.
- **EBA action:** Use `X-Auth-Token`, not Bearer. Add retry/refresh on 401.

### G-3. `/api/vms?expand=resources` ignores invalid attributes by **400-ing the whole request**
- **What:** When you pass an `attributes=` list, ManageIQ validates the names against its real schema. A wrong name (e.g. `memory_mb`, `vms_count`, `hosts_count`) doesn't get silently dropped — it returns `400 Api::BadRequestError: Invalid attributes specified: <name>`.
- **Why it matters:** Two consequences:
  1. You can't blindly request "everything" — you have to know what's there. Build the attribute list iteratively (request bare, then add).
  2. Doc pages list logical fields like memory in MB, but the **actual REST attribute name is different** (the right name for memory is `ram_size_in_bytes` — verify before relying on it).
- **EBA action:** Before adding an attribute to a query, fetch one resource with `?expand=resources&limit=1` (no `attributes=`) and look at the keys. The reference docs are slightly downstream of what's actually returned.

### G-4. **Adding a provider ≠ refreshing it.** `/api/vms` returns 0 even with 3 cloud providers configured.
- **What:** This appliance has AWS (real, `enbd-aws`), Azure synthetic (`enbd-azure-synthetic`), and OCI synthetic (`enbd-oci-synthetic`) providers configured — but `/api/vms` returns `count: 0`. No inventory has been pulled.
- **Why it matters:** A provider is just a connection record until you call **`POST /api/providers/:id` with `{"action": "refresh"}`**. Refresh is **asynchronous** — returns a task href; poll `/api/tasks/:id`. The UI hides this behind a button; the API doesn't.
- **EBA action:** Day-1 routine: for each provider, POST refresh, then poll task status until `state=Finished`. Document the typical refresh duration per provider type (AWS via the SDK is fast; Azure synthetic — depends on what's seeded).

### G-5. `count` in the chargebacks endpoint is *not* what it looks like
- **What:** `GET /api/chargebacks` returns `count: 3` — three **rate definitions** (Compute, Storage, default), not three chargeback **records** or cost rows. The "chargebacks" collection is the rate catalogue, not the calculated costs.
- **Why it matters:** "Chargeback module is enabled" doesn't mean cost data exists. Calculated costs live elsewhere (chargeback reports / metric rollups joined to rates), and the rates themselves default to `1.0 + 0.0 per Cpu` (the "Default" rate) — which produces real numbers that look meaningful and are not.
- **EBA action:** Before claiming a chargeback number, confirm the rate ID it was calculated against and whether that rate has real per-resource pricing or the placeholder defaults. The PoC's chargeback view must show the rate used.

### G-6. Self-signed TLS — pin the appliance cert, do NOT use `verify=False`
- **What:** The Quinteros docker-compose appliance generates a self-signed cert. The lazy reflex is `curl -k` / `requests.get(..., verify=False)`. We're not doing that.
- **Why it matters:** `verify=False` permits MITM and trains the EBA team to model the wrong pattern in a bank context. The synthetic appliance is local, but the *habit* travels — and a confidently-wrong cost figure delivered over a MITM'd channel is exactly the failure mode SPEC §0 calls out.
- **EBA action:**
  1. Export the appliance cert once: `openssl s_client -connect localhost:443 -servername localhost </dev/null 2>/dev/null | openssl x509 > miq_appliance.pem`.
  2. Point the collector at it: `requests.get(url, verify="/path/to/miq_appliance.pem")` (or env var `REQUESTS_CA_BUNDLE`).
  3. For production at ENBD: mount a cert issued by ENBD's internal CA into the appliance container and trust the CA bundle, not the leaf.
- **PoC code rule:** No `verify=False` and no `urllib3.disable_warnings` anywhere in the collector. If the cert path is missing, fail loudly with a clear error pointing at the export command above.

### G-8. Provider refresh returns `success: true` only if creds are valid — **no inventory exists otherwise**, even on synthetic providers
- **What:** `POST /api/providers/:id {"action":"refresh"}` on all three of our providers returns:
  ```json
  {"success": false, "message": "Provider failed last authentication check"}
  ```
- All three `authentications` records show `status: "Error"` or `"Invalid"`:
  - AWS (`enbd-aws`): `userid: "REPLACE_WITH_ACCESS_KEY_ID"` — placeholder string, never set
  - Azure (`enbd-azure-synthetic`): "Incorrect credentials - check your Azure Subscription ID"
  - OCI (`enbd-oci-synthetic`): "undefined method `gsub!' for nil:NilClass" — Ruby NPE from a missing config field
- **Why it matters:** Three layered traps:
  1. **The synthetic providers still go through real auth.** ManageIQ doesn't have "fake provider mode" — even an `enbd-azure-synthetic` connection runs Azure SDK calls and fails the same way a misconfigured real one would. To populate VMDB from synthetic data, you must EITHER (a) seed the VMDB directly via the Rails console / SQL / Automate, bypassing the cloud SDK, OR (b) stand up a fake-cloud endpoint (LocalStack for AWS; nothing comparable for Azure/OCI).
  2. **The OCI failure is a Ruby NPE, not a friendly error.** `undefined method gsub! for nil:NilClass` means a field the OCI provider gem assumes-non-null is null. The API surfaces it raw — a real ENBD ops user would see this and not know it's a config gap, not a bug.
  3. **AWS provider authtype `s3`** is a separate authentication record from the `default` one — adding a provider may create multiple cred slots, and refresh checks the `default` slot. Both slots need filling for full functionality.
- **EBA action:** Day 1, decide the synthetic-data approach BEFORE configuring providers. The PoC will go with **option (a): seed the VMDB directly** (via Rails console or SQL fixtures) because it makes the join problem visible at the same scale as production without needing LocalStack. Document this as the chosen path so ENBD's team doesn't waste hours trying to make Azure/OCI synthetic providers "auth correctly."

### G-9. Authentication errors are surfaced through `status_details`, but only AFTER the first failing refresh attempt
- **What:** A freshly-created provider's `authentications` record has `status: null`, not "Invalid." It only flips to "Error"/"Invalid" once refresh has been attempted. `last_invalid_on` is the timestamp of the most recent failure; `last_valid_on` stays null until something succeeds.
- **Why it matters:** A monitoring loop that only checks `status == "Invalid"` will think *all newly-added providers are healthy* — they're just unchecked. The correct readiness check is `last_valid_on IS NOT NULL AND status != 'Invalid'`.
- **EBA action:** Health check logic must distinguish "never refreshed" from "refresh succeeded." The PoC's data-source banner for the on-prem view should display the appliance's last successful refresh time per provider, not just a pass/fail.

### G-7. Provider listing returns *two or three* providers per cloud (CloudManager + NetworkManager + StorageManager)
- **What:** Adding "AWS" to ManageIQ creates **three** provider records:
  - `ManageIQ::Providers::Amazon::CloudManager`
  - `ManageIQ::Providers::Amazon::NetworkManager`
  - `ManageIQ::Providers::Amazon::StorageManager::Ebs`
- Azure and OCI each create two (Cloud + Network; no separate storage manager surfaced on this appliance).
- **Why it matters:** `/api/providers` count is multiplied. If you "filter to AWS providers" you'll get three, not one. Inventory belongs to the CloudManager — refresh on the others is a no-op for VM data but matters for networking/EBS detail.
- **EBA action:** Filter by `type` ending in `::CloudManager` when you want the cost-bearing provider. Document the manager hierarchy in the join logic.

---

## (Reserve sections — fill as we hit them)

## FOCUS normalization

### F-1. FOCUS v1.3 deprecates `Provider` and `Publisher` — use `ServiceProviderName` / `InvoiceIssuerName`
- **What:** Verified against FOCUS v1.3 column inventory (focus.finops.org MCP, 2026-06-25): `Provider` and `Publisher` carry a deprecation message ("deprecated in v1.3 and will be removed in v1.4"). Their replacements are `ServiceProviderName` and `InvoiceIssuerName` respectively (both added with v1.3).
- **Why it matters:** SPEC §1's table already cites `ServiceProviderName` correctly, but lots of older FinOps tutorials and AWS CUR2 sample mappings still emit `Provider`. The PoC's normalizer must consume the provider-native field but EMIT the v1.3-compliant name. If we ship the EBA team example code that emits `Provider`, they'll build forward and have to refactor a year later.
- **EBA action:** Target v1.3 columns from day 1. The PoC's `focus_costs` table uses `service_provider_name` and `invoice_issuer_name`. Reject `provider` / `publisher` as table columns even though the tools and tutorials still suggest them.

### F-2. `ServiceCategory` is mandatory and non-null — its allowed values are a closed set
- **What:** SPEC §1 cites `ServiceCategory='AI and Machine Learning'` as the allowed value for Bedrock-style rows. ServiceCategory is normative and the values are constrained by the spec's normative section (Service Category page). Any normalizer that emits a free-text string into this column produces a non-conforming FOCUS dataset.
- **Why it matters:** The Bedrock line items the spec requires (SPEC §3.1, requirement #1) will fail FOCUS conformance if the normalizer picks a casual category like `"GenAI"` or `"Bedrock"`. The exact normative string is what matters.
- **EBA action:** Hardcode the allowed-value list in the normalizer (fetch it from the FOCUS MCP rather than copying — the list grows with each spec version). Validate every row: ServiceCategory in (allowed set) and NOT NULL. Emit a validation report row-by-row.

## Provider-native → FOCUS mapping
_(nothing yet — start with Azure per SPEC §2)_

## FOCUS ↔ ManageIQ resource join

### J-1. The cloud↔MIQ join key is `vms.uid_ems`, NOT `vms.name`, NOT `vms.ems_ref`
- **What:** ManageIQ stores two cloud-side identifiers per VM:
  - **`vms.uid_ems`** — the cloud provider's resource instance ID. For AWS: the `i-0abc123...` instance ID. For Azure: the resource GUID. For OCI: the OCID. **This is the join key.**
  - **`vms.ems_ref`** — a secondary reference string the SDK uses. For AWS it often matches `uid_ems`; for Azure it may be the fully-qualified ARM path; for OCI it varies. **Do not use it as the primary join key** — its format is provider-dependent.
- The cloud-side cost data uses these identifiers too: FOCUS `ResourceId` ≈ `vms.uid_ems`. AWS CUR `lineItem/ResourceId` is the EC2 instance ID (= `uid_ems`). Azure cost export `ResourceId` is the ARM path (= `ems_ref` in many cases, NOT `uid_ems`).
- **Why it matters:** This is THE join problem from SPEC §2. Naive joiners try `vms.name = FOCUS.ResourceName`, which fails on every cloud because cloud cost data uses IDs, not names. Joining on the wrong field silently produces "0 rows matched" or, worse, wrong matches via name collisions across providers.
- **EBA action:** The join map MUST use:
  - AWS: `vms.uid_ems = focus.ResourceId` (both are `i-...`)
  - Azure: `vms.ems_ref = focus.ResourceId` (both are ARM paths) — different column!
  - OCI: `vms.uid_ems = focus.ResourceId` (both are OCIDs)
- **One column for AWS+OCI, a different column for Azure.** The PoC's `resource_join_map` table will materialize this asymmetry explicitly.

### J-2. ManageIQ's VMDB uses **MB** for memory and integer percentages for utilization — FOCUS doesn't carry utilization at all
- **What:** `hardwares.memory_mb` is bigint MB. `metric_rollups.cpu_usage_rate_average` is `double precision` 0–100 (percent). FOCUS has no equivalent column — there's no `Utilization` in any FOCUS version (verified earlier against the spec).
- **Why it matters:** Requirement #2 (resource utilization %) can never come from FOCUS. The PoC's utilization view is exclusively MIQ-sourced; the data-source banner must say so. This is exactly the "FOCUS partial" verdict in SPEC §1.
- **EBA action:** Don't try to extend FOCUS with a utilization column — that breaks the spec's normative requirements. Keep utilization in `miq_utilization` (joined to costs at the resource level via the join map), and surface it as "MIQ-sourced" everywhere.

### J-5. `metric_rollups` has `created_on` but **no `updated_on`** — inconsistent with the rest of the VMDB
- **What:** `vms`, `hardwares`, `chargeback_rates`, and most other VMDB tables have the standard Rails `created_on` + `updated_on` pair. `metric_rollups` only has `created_on`. Inserting `updated_on` produces `ERROR: column "updated_on" of relation "metric_rollups" does not exist` and aborts the whole transaction.
- **Why it matters:** Two things. First, anyone writing inventory seed code by analogy from `vms` will break on `metric_rollups` --- I did exactly this on the first run. Second, "the whole transaction aborts" with a single column error means downstream INSERTs all fail silently after the first one. If you wrap the seed in `BEGIN`, every subsequent error message is just `current transaction is aborted`; you must `grep -m1 ERROR` to find the real cause.
- **EBA action:** Never wrap inventory seeds in `BEGIN/COMMIT` while developing --- run statement-by-statement so the first real error is visible. Add a single `\set ON_ERROR_STOP on` at the top of psql scripts to abort *with the original error message*.

### J-4. `vms`, `hardwares`, `metric_rollups` have **almost no NOT NULL constraints** — direct SQL seeding works trivially but offers no safety net
- **What:** Inspected with `information_schema.columns` on the live Quinteros appliance:
  - `vms`: only `id` is NOT NULL.
  - `hardwares`: only `id` is NOT NULL.
  - `metric_rollups`: **zero** NOT NULL columns (not even `id` is declared NOT NULL — it has a default sequence but is technically nullable).
- **Why it matters:** ManageIQ validates inventory shape at the Rails model layer, not the DB layer. Direct INSERT into the VMDB bypasses every Rails validation: required-field checks, enum constraints, FK consistency, audit logging. Seeding via SQL is the fastest path to inventory, AND the fastest path to silent corruption.
- **EBA action:** Synthetic seed via SQL is fine for the PoC because the data is throwaway. For any real ENBD scenario, populate inventory via the Rails console (`rails runner`), Automate methods, or a fake-provider --- never via raw INSERTs in a path that lasts beyond a demo.

### J-6. Bedrock / Azure OpenAI / S3 / managed-service rows have NO MIQ inventory to join to — this is correct
- **What:** Running the resource_join_map against our seeded inventory leaves 6 FOCUS rows as `unmatched_focus_only`:
  - 3 Bedrock model ARNs (one per Claude/Nova model)
  - 1 Azure OpenAI account
  - 1 S3 bucket
  - 1 mystery `arn:aws:demo:???` (blank-ProductName messiness row)
- **Why it matters:** It is tempting to flag this as a join failure. It is not. Bedrock foundation models, S3 buckets, and managed-service endpoints are NOT VMs --- they have no inventory representation in ManageIQ's `vms` collection at all. The join is correctly reporting "this cost row has no MIQ resource to attribute to." A bad UI would hide these rows; the correct UI explicitly shows their cost as "managed services, not attributable to a VM."
- **EBA action:**
  1. The presentation page must treat `unmatched_focus_only` rows differently by ServiceCategory. AI/ML/Storage/Networking rows here are expected (managed services). Compute rows here are a real problem (compute exists in cloud cost but not in MIQ inventory) and should surface as a "missing inventory" warning.
  2. The PoC's AI-cost view (requirement #1) lives entirely in this `unmatched_focus_only` slice. The view groups Bedrock rows by `SkuMeter` (which carries the model id thanks to the normalizer's enrichment in `aws_to_focus.py`).
  3. The mystery `arn:aws:demo:???` row is the SPEC s3.1 blank-ProductName messiness landing as a join orphan --- that's the desired behavior.

### J-3. `metric_rollups` is polymorphic via `resource_type` + `resource_id`
- **What:** `metric_rollups.resource_type` is `"VmOrTemplate"` (or `"Host"`, `"ContainerProject"`, etc.); `resource_id` is the FK into that table. There's no direct `vm_id` column.
- **Why it matters:** Joins against rollups need `resource_type='VmOrTemplate' AND resource_id=vms.id`. Skipping the type predicate joins against Hosts and Containers too and double-counts.
- **EBA action:** Always pin `resource_type` in utilization joins.

## Postgres / data layer

### D-1. Sharing the appliance's Postgres server violates SPEC §2's portability invariant — we did it anyway for the PoC, with an exit ramp
- **What:** Slice 4 puts the PoC's `focus` database into the *same Postgres server* that runs ManageIQ's `vmdb_production`. The connection point is the appliance's port 5432. This is a deliberate shortcut: one fewer container, one fewer credential surface, and we get a real-Postgres target without standing up a new docker-compose service.
- **Why it matters:** SPEC §2 mandates portability — the data layer must run on-prem OR AWS, untangled from ManageIQ. Co-locating with `vmdb_production` couples our schema to whatever Postgres version the appliance ships, and an `apt upgrade` of ManageIQ could break us. It also means a `docker rm manageiq_appliance` deletes our cost data.
- **EBA action (day 1 of the sprint):** Split the FOCUS DB into its own container. The `db/` directory's schema.sql is portable as-is; only the connection string changes. The loader honors `FOCUS_PGHOST` / `FOCUS_PGPORT` / `FOCUS_PGUSER` / `FOCUS_PGPASS` env vars so the move is a config change, not a code change.
- **Recovery cost if you forget:** zero — the schema and data are throwaway. The risk is silent coupling getting reproduced in the EBA team's first real build.

### D-2. `psql -c "CREATE DATABASE ..."` fails inside a multi-statement block: "CREATE DATABASE cannot run inside a transaction block"
- **What:** When you feed `psql` a string with multiple semicolons via `-c`, psql wraps the whole thing in an implicit transaction. `CREATE DATABASE`, `CREATE TABLESPACE`, `REINDEX DATABASE`, and a few others are non-transactional and refuse. The error doesn't mention `-c`'s wrapping behavior — it just says "transaction block," which is misleading.
- **Why it matters:** Standard "set up the DB" scripts hit this. The fix is one statement per `-c` call (auto-commit), OR a `.sql` file with `-f` and no enclosing BEGIN/COMMIT.
- **EBA action:** Setup scripts that need both role and DB creation should use multiple `psql -c` invocations (one per DDL statement), or `psql -f script.sql` with no transaction wrapper for the CREATE DATABASE line.

### D-4. `COPY FROM file` requires Postgres superuser; use `\COPY` from psql instead
- **What:** First attempt at loading `focus_costs` failed with `ERROR: must be superuser or a member of the pg_read_server_files role to COPY from a file`. This is server-side COPY, which reads files from the Postgres server's filesystem and is privileged. The `pg_read_server_files` role is exactly what its name implies and you don't want to grant it to a workload user.
- **Why it matters:** psql's lowercase `copy` and SQL's uppercase `COPY` look identical at a glance, but psql's backslash command `\COPY` (note the backslash) is a CLIENT-side operation that streams the file via STDIN — any user with INSERT privilege on the target table can run it. The error message hints at `\copy` but easy to miss.
- **EBA action:** Use `\COPY` (psql client-side) for all loader paths. Reserve `COPY FROM file` for admin scripts running as `postgres`. The loader in `db/loader.py` does this; preserve the pattern.

### D-3. Appliance Postgres is not port-mapped to the host
- **What:** `docker inspect manageiq_appliance` shows port 443 mapped (the HTTPS API), 3000 and 4000 left unmapped, and no entry for 5432. Inside the container, Postgres listens on 0.0.0.0:5432 — but the host can't reach it.
- **Why it matters:** The loader needs to talk to Postgres. Two paths: (a) `docker exec manageiq_appliance psql -U focus_app -d focus -f script.sql`, which works but requires docker access on the host running the loader; (b) re-create the appliance container with `-p 5432:5432`, but the appliance image likely doesn't expose 5432 in its Dockerfile so this needs explicit configuration.
- **EBA action:** For the PoC, the loader uses `docker exec` (path a). For production, run the FOCUS DB in its own container per D-1 — at which point this gotcha evaporates.

## On-prem cost / chargeback
_(see G-5; more once we wire the join to rates)_

## Bedrock / NL-query layer
_(deferred — built last)_

## Carbon stub
_(deferred)_

## Web layer

### W-1. Starlette 1.x flipped `Jinja2Templates.TemplateResponse` to request-first
- **What:** Calling `templates.TemplateResponse("index.html", {"request": request, ...})` on Starlette 1.3.1 (the version pulled in by FastAPI 0.138) fails with `TypeError: unhashable type: 'dict'`. The dict gets passed where the request was expected; starlette then tries to use the request slot as the template name and the dict ends up inside jinja's cache key lookup.
- **Why it matters:** Every FastAPI tutorial older than 2024 shows the dict-first form. The error message points at jinja2's `LRUCache.__getitem__` — three frames away from the real bug, with no hint that the call site is wrong. Easy to chase for an hour.
- **EBA action:** Always use the request-first form:
  ```python
  templates.TemplateResponse(request, "index.html", {"key": value})
  ```
  If you can't avoid older code, pin `starlette<0.27` (FastAPI <0.110 implicitly does this), but the right answer is to update the call sites.

## Docker-compose / portability
_(nothing yet)_
