# Real MVP — Spec 1: Config-driven upload ingestion + realistic synthetic data — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the seed-a-CSV pipeline into a config-driven *upload* ingestion product running on realistic synthetic FOCUS data, behind the existing `SourceAdapter` contract, with API-pull connectors registered-but-stubbed.

**Architecture:** The pipeline `registry → dispatcher → adapter.discover()/normalize() → focus_combined.csv → loader → join → dashboard` is unchanged in shape. We (a) upgrade the generators to emit real-shaped data, (b) add a real `UploadSource` whose `discover()` lists an upload-inbox directory, (c) add a `POST /connect/upload` endpoint that validates FOCUS-conformance before accepting a file, (d) register stubbed API-pull adapters, and (e) make the dispatcher fail-soft. The contract dataclasses, `focus_combined.csv` shape, join, and conformance validator do not change — that the boundary absorbs this is the proof it is right.

**Tech Stack:** Python 3.11, FastAPI + Jinja2, psycopg2, Postgres 16, pytest, docker-compose. Deterministic seeded RNG (`generators/common.make_rng()`).

## Global Constraints

- Synthetic data only; every row obviously fake — `DEMO-` names, fake account IDs (`FAKE_AWS_ACCOUNT_ID="999900001111"`, `FAKE_AZURE_SUBSCRIPTION`, `FAKE_OCI_TENANCY` in `generators/common.py`). Never real AnyBank data, never real creds.
- Reporting currency is USD. **Never SUM mixed currencies** — always `billed_cost_usd`. Azure bills AED / prices USD (the B-6/B-7 bug class); keep that split.
- FOCUS `ServiceCategory` is a closed, case-sensitive set (`normalizer/focus_spec.SERVICE_CATEGORIES_V1_3`). Non-conformant rows are reported and dropped, never invented.
- All generator output is deterministic (seed `RNG_SEED=20260625`) so tests assert exact counts. Parameterized by `FOCUS_GEN_DAYS` (exists) and new `FOCUS_GEN_SCALE`.
- Honesty discipline: no control may look live when it isn't. Stubbed API-pull sources must render visibly disabled with a "later release" label.
- Certificate verification never disabled, no disabling TLS anywhere.
- Secrets only via env / Secrets Manager reference strings — never inline, never committed (the `secret-guard.sh` hook blocks on hit).
- Do not edit `SPEC.md`. Commit on the current feature branch; do not push without the user asking.
- Capture non-obvious findings in `GOTCHAS.md` as you go — it is the primary deliverable.

---

### Task 1: FOCUS spec constants — commitment columns + ChargeCategory closed set

**Files:**
- Modify: `normalizer/focus_spec.py`
- Test: `tests/test_focus_spec.py` (create)

**Interfaces:**
- Consumes: nothing (foundation task).
- Produces:
  - `focus_spec.CHARGE_CATEGORIES_V1_3: set[str]` = `{"Usage","Purchase","Tax","Credit","Adjustment","Refund"}`
  - `focus_spec.FOCUS_COLUMNS_V1_3` extended with `"CommitmentDiscountId"` and `"CommitmentDiscountStatus"` appended after `"Tags"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_focus_spec.py`:

```python
"""Pure-logic tests for the FOCUS spec constants (no DB)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalizer import focus_spec


def test_charge_categories_closed_set():
    # FOCUS v1.3 ChargeCategory closed set (spec 3.x). Case-sensitive.
    assert focus_spec.CHARGE_CATEGORIES_V1_3 == {
        "Usage", "Purchase", "Tax", "Credit", "Adjustment", "Refund",
    }


def test_commitment_columns_present():
    cols = focus_spec.FOCUS_COLUMNS_V1_3
    assert "CommitmentDiscountId" in cols
    assert "CommitmentDiscountStatus" in cols
    # Tags stays present; commitment columns come after it.
    assert cols.index("CommitmentDiscountId") > cols.index("Tags")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_focus_spec.py -v`
Expected: FAIL — `AttributeError: module 'normalizer.focus_spec' has no attribute 'CHARGE_CATEGORIES_V1_3'`.

- [ ] **Step 3: Add the constants**

In `normalizer/focus_spec.py`, after the `SERVICE_CATEGORIES_V1_3` block add:

```python
# Closed set of allowed ChargeCategory values per FOCUS v1.3. Case-sensitive.
# Real exports are NOT all "Usage" — taxes, commitment purchases, credits,
# refunds and adjustments all appear and break naive SUM(BilledCost).
CHARGE_CATEGORIES_V1_3 = {
    "Usage",
    "Purchase",
    "Tax",
    "Credit",
    "Adjustment",
    "Refund",
}
```

Then append the two commitment columns to `FOCUS_COLUMNS_V1_3`, right after `"Tags"`:

```python
    # Tagging
    "Tags",
    # Commitment discounts (Savings Plans / Reserved / OCI commitments). Their
    # presence is why EffectiveCost can differ from BilledCost/ListCost.
    "CommitmentDiscountId",
    "CommitmentDiscountStatus",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_focus_spec.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add normalizer/focus_spec.py tests/test_focus_spec.py
git commit -m "feat(focus): add ChargeCategory closed set + commitment-discount columns"
```

---

### Task 2: Normalizer — validate ChargeCategory, carry commitment columns

**Files:**
- Modify: `normalizer/focus_native_to_focus.py`
- Test: `tests/test_normalizer_native.py` (create)

**Interfaces:**
- Consumes: `focus_spec.CHARGE_CATEGORIES_V1_3`, extended `focus_spec.FOCUS_COLUMNS_V1_3` (Task 1).
- Produces: `focus_native_to_focus.map_row(row) -> (out, warnings)` now also flags fatal when `ChargeCategory` is non-empty but outside the closed set, and passes `CommitmentDiscountId`/`CommitmentDiscountStatus` through (already covered by the `TARGET_COLUMNS` loop, so this is validation only).

- [ ] **Step 1: Write the failing test**

Create `tests/test_normalizer_native.py`:

```python
"""Pure-logic tests for the native-FOCUS normalizer's validation."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalizer import focus_native_to_focus as n


def _row(**over):
    base = {
        "ServiceCategory": "Compute",
        "BillingCurrency": "USD",
        "ChargeCategory": "Usage",
    }
    base.update(over)
    return base


def test_valid_charge_category_passes():
    out, warns = n.map_row(_row())
    assert out["_fatal"] is False
    assert warns == []


def test_bad_charge_category_is_fatal():
    out, warns = n.map_row(_row(ChargeCategory="Banana"))
    assert out["_fatal"] is True
    assert any("ChargeCategory" in w for w in warns)


def test_empty_charge_category_is_allowed():
    # ChargeCategory empty is not fatal here (only ServiceCategory/currency are
    # FOCUS-mandatory in our gate); empty simply isn't validated against the set.
    out, warns = n.map_row(_row(ChargeCategory=""))
    assert out["_fatal"] is False


def test_commitment_columns_pass_through():
    out, _ = n.map_row(_row(CommitmentDiscountId="sp-demo-1",
                            CommitmentDiscountStatus="Used"))
    assert out["CommitmentDiscountId"] == "sp-demo-1"
    assert out["CommitmentDiscountStatus"] == "Used"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_normalizer_native.py -v`
Expected: FAIL — `test_bad_charge_category_is_fatal` fails (the normalizer does not yet validate ChargeCategory, so `_fatal` is False).

- [ ] **Step 3: Add ChargeCategory validation**

In `normalizer/focus_native_to_focus.py`, inside `map_row`, after the `BillingCurrency` check and before `out["_fatal"] = fatal`, add:

```python
    # ChargeCategory: if present, must be in the FOCUS closed set. Empty is
    # left alone (not all rows carry it); a wrong value is a fatal drop.
    cc = (out.get("ChargeCategory") or "").strip()
    if cc and cc not in focus_spec.CHARGE_CATEGORIES_V1_3:
        warnings.append(f"ChargeCategory {cc!r} not in FOCUS closed set")
        fatal = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_normalizer_native.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add normalizer/focus_native_to_focus.py tests/test_normalizer_native.py
git commit -m "feat(normalizer): validate ChargeCategory against FOCUS closed set"
```

---

### Task 3: Schema + loader — persist commitment-discount columns

**Files:**
- Modify: `db/schema.sql`
- Modify: `db/loader.py:53-91` (the `FOCUS_CSV_TO_DB_COLUMN` map)
- Test: covered by Task 12's data-integrity run (no isolated unit test — this is schema/mapping wiring verified end-to-end at load).

**Interfaces:**
- Consumes: extended `FOCUS_COLUMNS_V1_3` (Task 1) — these column names appear in `focus_combined.csv` headers written by the dispatcher.
- Produces: `focus_costs.commitment_discount_id TEXT`, `focus_costs.commitment_discount_status TEXT`; loader maps `CommitmentDiscountId`/`CommitmentDiscountStatus` into them.

- [ ] **Step 1: Add the columns to the schema**

In `db/schema.sql`, in the `focus_costs` table, after the `tags JSONB,` line (and before the `extensions JSONB,` comment block) add:

```sql
    -- Commitment discount identity (Savings Plans / Reserved / OCI commitments).
    -- Why EffectiveCost can diverge from BilledCost/ListCost (FinOps coverage story).
    commitment_discount_id      TEXT,                          -- CommitmentDiscountId
    commitment_discount_status  TEXT,                          -- CommitmentDiscountStatus
```

- [ ] **Step 2: Map them in the loader**

In `db/loader.py`, in `FOCUS_CSV_TO_DB_COLUMN`, after the `"Tags": "tags",` entry add:

```python
    "CommitmentDiscountId": "commitment_discount_id",
    "CommitmentDiscountStatus": "commitment_discount_status",
```

- [ ] **Step 3: Verify schema applies cleanly**

Run (compose stack up — `docker compose up -d db`):

```bash
docker compose exec -T db psql -U focus_app -d focus -f - < db/schema.sql && \
docker compose exec -T db psql -U focus_app -d focus -c "\d focus_costs" | grep commitment
```

Expected: two rows — `commitment_discount_id | text` and `commitment_discount_status | text`.

- [ ] **Step 4: Verify py_compile (loader change)**

Run: `.venv/bin/python -m py_compile db/loader.py`
Expected: no output (success).

- [ ] **Step 5: Commit**

```bash
git add db/schema.sql db/loader.py
git commit -m "feat(db): persist commitment-discount columns in focus_costs"
```

---

### Task 4: Generator foundations — scale knob, more accounts, realism helpers

**Files:**
- Modify: `generators/common.py`
- Test: `tests/test_generators_common.py` (create)

**Interfaces:**
- Consumes: existing `common.WORKLOADS`, `common.make_rng()`, `common.usd_to_aed()`.
- Produces, all in `generators.common`:
  - `gen_scale() -> int` — reads `FOCUS_GEN_SCALE` env (default 1), min 1. Multiplies per-day row fan-out.
  - `SUB_ACCOUNTS: dict[str, list[str]]` — provider → list of DEMO sub-account ids (≥3 each) so spend spreads across accounts.
  - `tag_sparsity(rng, tags) -> str` — returns a JSON tags string applying realistic sparsity: ~20% full, ~50% `env`-only, ~20% empty `{}`, ~10% malformed (`"{bad json"`). Deterministic given `rng`.
  - `commitment_fields(rng) -> tuple[str, str]` — returns `(CommitmentDiscountId, CommitmentDiscountStatus)`; ~30% of the time a real `("sp-DEMO-####","Used")`, else `("","")`.
  - `effective_spread(rng, billed_usd, has_commitment) -> tuple[float, float, float]` — returns `(effective, list_, contracted)` USD: with commitment, effective ≈ 0.7×billed, list = billed, contracted ≈ 0.72×billed; without, all equal billed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_generators_common.py`:

```python
"""Pure-logic tests for the generator realism helpers (no DB, deterministic)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators import common


def test_gen_scale_default_and_env(monkeypatch):
    monkeypatch.delenv("FOCUS_GEN_SCALE", raising=False)
    assert common.gen_scale() == 1
    monkeypatch.setenv("FOCUS_GEN_SCALE", "5")
    assert common.gen_scale() == 5
    monkeypatch.setenv("FOCUS_GEN_SCALE", "0")
    assert common.gen_scale() == 1  # floored at 1


def test_sub_accounts_present():
    for p in ("aws", "azure", "oci"):
        assert len(common.SUB_ACCOUNTS[p]) >= 3
        assert all(s.startswith("DEMO-") or "demo" in s.lower() for s in common.SUB_ACCOUNTS[p])


def test_tag_sparsity_is_deterministic_and_varied():
    rng = common.make_rng()
    out = [common.tag_sparsity(rng, {"app": "x", "env": "prod"}) for _ in range(200)]
    # deterministic: same seed reproduces
    rng2 = common.make_rng()
    out2 = [common.tag_sparsity(rng2, {"app": "x", "env": "prod"}) for _ in range(200)]
    assert out == out2
    # variety: at least one empty, one env-only, one malformed appears
    assert "{}" in out
    assert any(s == '{"env":"prod"}' for s in out)
    assert any(not _is_json(s) for s in out)  # malformed present


def _is_json(s):
    try:
        json.loads(s)
        return True
    except ValueError:
        return False


def test_commitment_fields_deterministic():
    rng = common.make_rng()
    pairs = [common.commitment_fields(rng) for _ in range(100)]
    used = [p for p in pairs if p[0]]
    assert used, "expected some commitment rows"
    assert all(p[1] == "Used" for p in used)


def test_effective_spread_with_commitment_is_discounted():
    eff, lst, con = common.effective_spread(common.make_rng(), 100.0, True)
    assert eff < 100.0 and lst == 100.0 and con < 100.0
    eff2, lst2, con2 = common.effective_spread(common.make_rng(), 100.0, False)
    assert eff2 == lst2 == con2 == 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_generators_common.py -v`
Expected: FAIL — `AttributeError: module 'generators.common' has no attribute 'gen_scale'`.

- [ ] **Step 3: Implement the helpers**

In `generators/common.py`, after `def make_rng()` add:

```python
def gen_scale() -> int:
    """Per-day row fan-out multiplier. FOCUS_GEN_SCALE=1 (default) is the
    fast, hand-traceable / CI size; the demo uses a larger value. Floored at 1."""
    import os
    try:
        return max(1, int(os.environ.get("FOCUS_GEN_SCALE", "1")))
    except ValueError:
        return 1


# Multiple sub-accounts per provider so spend spreads across accounts the way a
# real payer/management-group/tenancy does. All obviously DEMO.
SUB_ACCOUNTS: dict[str, list[str]] = {
    "aws":   ["DEMO-prod-9001", "DEMO-nonprod-9002", "DEMO-shared-9003", "DEMO-data-9004"],
    "azure": ["rg-prod-demo", "rg-nonprod-demo", "rg-shared-demo", "rg-data-demo"],
    "oci":   ["DEMO-cmp-prod", "DEMO-cmp-nonprod", "DEMO-cmp-analytics"],
}


def tag_sparsity(rng: random.Random, tags: dict) -> str:
    """Real tag coverage is partial. Return a JSON tag string with realistic
    sparsity buckets (deterministic given rng):
      ~20% fully tagged · ~50% env-only · ~20% empty {} · ~10% malformed."""
    import json
    roll = rng.random()
    if roll < 0.20:
        return json.dumps(tags, separators=(",", ":"))
    if roll < 0.70:
        env = tags.get("env", "prod")
        return json.dumps({"env": env}, separators=(",", ":"))
    if roll < 0.90:
        return "{}"
    return '{bad json'  # malformed on purpose — stresses the tag parser


def commitment_fields(rng: random.Random) -> tuple[str, str]:
    """~30% of compute rows are covered by a commitment. Returns
    (CommitmentDiscountId, CommitmentDiscountStatus)."""
    if rng.random() < 0.30:
        return (f"sp-DEMO-{rng.randint(1000, 9999)}", "Used")
    return ("", "")


def effective_spread(rng: random.Random, billed_usd: float,
                     has_commitment: bool) -> tuple[float, float, float]:
    """Return (effective, list, contracted) USD. A commitment discounts
    EffectiveCost/ContractedCost below BilledCost; ListCost is the on-demand
    rate (== billed here). No commitment → all equal billed."""
    if not has_commitment:
        return (billed_usd, billed_usd, billed_usd)
    eff = round(billed_usd * 0.70, 6)
    con = round(billed_usd * 0.72, 6)
    return (eff, billed_usd, con)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_generators_common.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add generators/common.py tests/test_generators_common.py
git commit -m "feat(generators): realism helpers — scale, sub-accounts, tag sparsity, commitments"
```

---

### Task 5: Realistic native-FOCUS generators

**Files:**
- Modify: `generators/focus_native.py`
- Test: `tests/test_focus_native_realism.py` (create)

**Interfaces:**
- Consumes: Task 4 helpers (`gen_scale`, `SUB_ACCOUNTS`, `tag_sparsity`, `commitment_fields`, `effective_spread`), `focus_spec.CHARGE_CATEGORIES_V1_3`, extended `FOCUS_COLUMNS_V1_3`.
- Produces: `generate_aws/azure/oci(days)` emit, per day, the existing usage rows PLUS, scaled by `gen_scale()`: multi-sub-account spread, a `Tax` row, a `Purchase` (commitment) row, a `Credit` row, a `Refund` row, commitment-covered compute rows with the cost spread, tag sparsity applied to compute rows, and a *scaled small fraction* of dirty rows. Counts are deterministic.

Note: keep the existing AI/Bedrock rows and the existing single duplicate + null-ServiceCategory dirty rows. The new charge-category and commitment rows are additive. ResourceId still carries the J-1 join key on compute rows.

- [ ] **Step 1: Write the failing test**

Create `tests/test_focus_native_realism.py`:

```python
"""Realism tests for the native-FOCUS generators. Deterministic at SCALE=1."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators import focus_native
from normalizer import focus_spec


def _rows(monkeypatch, gen, scale="1", days=2):
    monkeypatch.setenv("FOCUS_GEN_SCALE", scale)
    rows, cols = gen(days=days)
    return rows, cols


def test_full_charge_category_mix_present(monkeypatch):
    rows, _ = _rows(monkeypatch, focus_native.generate_aws)
    cats = {r.get("ChargeCategory", "") for r in rows}
    for needed in ("Usage", "Tax", "Purchase", "Credit", "Refund"):
        assert needed in cats, f"missing ChargeCategory {needed}"


def test_commitment_rows_have_cost_spread(monkeypatch):
    rows, _ = _rows(monkeypatch, focus_native.generate_aws)
    covered = [r for r in rows if str(r.get("CommitmentDiscountId", ""))]
    assert covered, "expected commitment-covered rows"
    # at least one row where EffectiveCost < BilledCost (real coverage)
    assert any(float(r["EffectiveCost"]) < float(r["BilledCost"]) for r in covered)


def test_scale_multiplies_volume(monkeypatch):
    small, _ = _rows(monkeypatch, focus_native.generate_aws, scale="1")
    big, _ = _rows(monkeypatch, focus_native.generate_aws, scale="4")
    assert len(big) > len(small)


def test_deterministic(monkeypatch):
    a, _ = _rows(monkeypatch, focus_native.generate_azure)
    b, _ = _rows(monkeypatch, focus_native.generate_azure)
    assert len(a) == len(b)
    assert [r.get("BilledCost") for r in a] == [r.get("BilledCost") for r in b]


def test_azure_keeps_mixed_currency(monkeypatch):
    rows, _ = _rows(monkeypatch, focus_native.generate_azure)
    usage = [r for r in rows if r.get("ChargeCategory") == "Usage"
             and r.get("ServiceCategory") == "Compute"]
    assert any(r.get("BillingCurrency") == "AED" for r in usage)


def test_columns_include_commitment(monkeypatch):
    _, cols = _rows(monkeypatch, focus_native.generate_aws)
    assert "CommitmentDiscountId" in cols
    assert "CommitmentDiscountStatus" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_focus_native_realism.py -v`
Expected: FAIL — `test_full_charge_category_mix_present` (only `Usage` is emitted today) and `test_columns_include_commitment`.

- [ ] **Step 3: Implement realism in the generators**

In `generators/focus_native.py`:

(a) Extend each provider's `cols` to include the commitment columns. Change the three `cols = focus_spec.FOCUS_COLUMNS_V1_3 + AWS_X_COLUMNS` lines (and Azure/OCI equivalents) — they already pick up the new commitment columns automatically because `FOCUS_COLUMNS_V1_3` was extended in Task 1, so **no edit needed** there; the test `test_columns_include_commitment` passes once Task 1 is in. Confirm by reading: `cols` already equals `FOCUS_COLUMNS_V1_3 + X`.

(b) Add a shared helper near the top of the module (after the `_period` function):

```python
def _non_usage_rows(rng, account_id, account_name, bps, bpe, cps, cpe,
                    provider_name, issuer, currency, x_extra):
    """The charge categories real exports carry beyond Usage: a monthly Tax
    line, a commitment Purchase, a Credit, and a Refund. Small, fixed set per
    day — scaled by the caller. `x_extra` is the provider x_ dict to merge."""
    out = []
    specs = [
        ("Tax",      "Management and Governance", "VAT on cloud services", 12.50),
        ("Purchase", "Compute",                   "Compute Savings Plan (1yr, no upfront)", 240.00),
        ("Credit",   "Other",                     "Promotional credit", -35.00),
        ("Refund",   "Compute",                   "Refund — overcharge correction", -8.75),
    ]
    for cat, svc_cat, desc, amount in specs:
        r = _base_row()
        r.update({
            "BillingAccountId": account_id, "BillingAccountName": account_name,
            "BillingPeriodStart": bps, "BillingPeriodEnd": bpe,
            "ChargePeriodStart": cps, "ChargePeriodEnd": cpe,
            "ChargeCategory": cat, "ChargeDescription": desc,
            "BilledCost": amount, "EffectiveCost": amount,
            "ListCost": amount, "ContractedCost": amount,
            "BillingCurrency": currency, "PricingCurrency": "USD",
            "ServiceProviderName": provider_name, "InvoiceIssuerName": issuer,
            "ServiceCategory": svc_cat, "ServiceName": "Account-level charge",
            "ChargeFrequency": "One-Time" if cat in ("Credit", "Refund") else "Recurring",
        })
        r.update(x_extra)
        out.append(r)
    return out
```

(c) In `generate_aws`, inside the `for d in range(days):` day loop, after the workload `for wl in aws_wls:` loop, apply scale + sub-account spread + tag sparsity + commitments to the compute rows. Replace the compute-row construction so it:
  - wraps the inner body in `for _ in range(common.gen_scale()):` and picks `sub = rng.choice(common.SUB_ACCOUNTS["aws"])` for `SubAccountId`/`SubAccountName`;
  - sets `cid, cstatus = common.commitment_fields(rng)` and `eff, lst, con = common.effective_spread(rng, cost, bool(cid))`, then writes `"EffectiveCost": eff, "ListCost": lst, "ContractedCost": con, "CommitmentDiscountId": cid, "CommitmentDiscountStatus": cstatus`;
  - sets `"Tags": common.tag_sparsity(rng, wl.tags)` instead of the plain dump.

Concretely, the AWS compute block becomes:

```python
        for wl in aws_wls:
            for _ in range(common.gen_scale()):
                sub = rng.choice(common.SUB_ACCOUNTS["aws"])
                cost = round(24 * (0.05 * wl.cpu_cores) + rng.uniform(-0.1, 0.1), 6)
                cid, cstatus = common.commitment_fields(rng)
                eff, lst, con = common.effective_spread(rng, cost, bool(cid))
                r = _base_row()
                r.update({
                    "BillingAccountId": common.FAKE_AWS_ACCOUNT_ID,
                    "BillingAccountName": "DEMO-AnyBank-AWS",
                    "SubAccountId": sub, "SubAccountName": sub,
                    "BillingPeriodStart": bps, "BillingPeriodEnd": bpe,
                    "ChargePeriodStart": cps, "ChargePeriodEnd": cpe,
                    "ChargeCategory": "Usage", "ChargeClass": "",
                    "ChargeDescription": f"EC2 demo.{wl.cpu_cores}xlarge",
                    "BilledCost": cost, "EffectiveCost": eff, "ListCost": lst, "ContractedCost": con,
                    "BillingCurrency": "USD", "PricingCurrency": "USD",
                    "ServiceProviderName": "AWS", "InvoiceIssuerName": "Amazon Web Services, Inc.",
                    "ServiceCategory": "Compute", "ServiceName": "Amazon Elastic Compute Cloud",
                    "SkuId": f"demo.{wl.cpu_cores}xlarge", "SkuMeter": "BoxUsage",
                    "ResourceId": wl.aws_instance_id,
                    "ResourceName": wl.name_in_provider("aws"),
                    "ResourceType": "Instance",
                    "RegionId": "me-central-1", "RegionName": "Middle East (UAE)",
                    "ConsumedQuantity": 24, "ConsumedUnit": "Hrs",
                    "PricingQuantity": 24, "PricingUnit": "Hrs",
                    "Tags": common.tag_sparsity(rng, wl.tags),
                    "CommitmentDiscountId": cid, "CommitmentDiscountStatus": cstatus,
                    "x_Discounts": "0", "x_Operation": "RunInstances", "x_ServiceCode": "AmazonEC2",
                })
                rows.append(r)
        # account-level non-usage charges (Tax/Purchase/Credit/Refund)
        rows.extend(_non_usage_rows(
            rng, common.FAKE_AWS_ACCOUNT_ID, "DEMO-AnyBank-AWS", bps, bpe, cps, cpe,
            "AWS", "Amazon Web Services, Inc.", "USD",
            {"x_Discounts": "0", "x_Operation": "", "x_ServiceCode": "AccountCharge"}))
```

(d) Apply the equivalent edits to `generate_azure` (keep its AED billing / USD pricing on the Usage compute rows — pass `common.usd_to_aed(cost_usd)` to BilledCost and the AED `effective_spread` inputs in AED; pass `currency="AED"` to `_non_usage_rows`) and `generate_oci` (USD; `currency="USD"`). Wrap their compute loops in `for _ in range(common.gen_scale()):`, choose `SubAccountId` from `common.SUB_ACCOUNTS["azure"|"oci"]`, apply `tag_sparsity`, `commitment_fields`, `effective_spread`, and append `_non_usage_rows(...)` per day.

For Azure, the spread must stay in AED to match BilledCost; compute it as:
```python
                cost_usd = round(24 * (1.2 * wl.cpu_cores / 24) + rng.uniform(-0.1, 0.1), 6)
                billed_aed = common.usd_to_aed(cost_usd)
                cid, cstatus = common.commitment_fields(rng)
                eff, lst, con = common.effective_spread(rng, billed_aed, bool(cid))
```
then set `"BilledCost": billed_aed, "EffectiveCost": eff, "ListCost": lst, "ContractedCost": con` and `"ListCost"` should be the USD list only if you prefer — keep `lst` (AED) for internal consistency so the currency column stays truthful (`BillingCurrency="AED"`).

(e) Keep the existing duplicate row and null-ServiceCategory dirty row at the end of `generate_aws` unchanged (they are the controlled defects the validator must catch). Do not scale them — one of each is enough and keeps the drop count assertable.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_focus_native_realism.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the existing generator entrypoint and eyeball volume**

Run: `FOCUS_GEN_DAYS=3 FOCUS_GEN_SCALE=10 .venv/bin/python -m generators.focus_native`
Expected: three `focus_*.csv` paths printed with row counts visibly larger than before (hundreds–thousands).

- [ ] **Step 6: Commit**

```bash
git add generators/focus_native.py tests/test_focus_native_realism.py
git commit -m "feat(generators): realistic volume, charge-category mix, commitments, tag sparsity"
```

---

### Task 6: UploadSource adapter — inbox discover() with watermark

**Files:**
- Modify: `connectors/adapters.py`
- Test: `tests/test_upload_source.py` (create)

**Interfaces:**
- Consumes: `connectors.contract.SourceConfig`, `DiscoveredExport`, `NormalizeResult`; `normalizer.focus_native_to_focus`.
- Produces:
  - `connectors.adapters.UPLOAD_ROOT` = `<ROOT>/out/uploads`
  - `connectors.adapters.inbox_dir(source_id) -> str` (creates `out/uploads/<source_id>/`)
  - class `UploadSource` with `source_type = "upload-focus"`, `discover(cfg)` listing `*.csv` in the source's inbox newer than the watermark file `.watermark` (mtime), `normalize(cfg, export)` delegating to `focus_native_to_focus` and tagging `_source = "upload"`.
  - Registered in `ADAPTERS` under `"upload-focus"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_upload_source.py`:

```python
"""Tests for the UploadSource adapter (filesystem inbox, watermark)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors import adapters
from connectors.contract import SourceConfig


def _cfg(sid):
    return SourceConfig(source_id=sid, source_type="upload-focus",
                        display_name=sid, location="", credential_ref="demo",
                        schedule="manual")


def test_inbox_dir_created(tmp_path, monkeypatch):
    monkeypatch.setattr(adapters, "UPLOAD_ROOT", str(tmp_path))
    d = adapters.inbox_dir("src-1")
    assert os.path.isdir(d)
    assert d.endswith(os.path.join("src-1"))


def test_discover_lists_csv_files(tmp_path, monkeypatch):
    monkeypatch.setattr(adapters, "UPLOAD_ROOT", str(tmp_path))
    d = adapters.inbox_dir("src-2")
    with open(os.path.join(d, "export.csv"), "w") as f:
        f.write("ServiceCategory,BillingCurrency\nCompute,USD\n")
    src = adapters.UploadSource()
    found = src.discover(_cfg("src-2"))
    assert len(found) == 1
    assert found[0].export_id == "export.csv"


def test_registered_in_adapters():
    assert "upload-focus" in adapters.ADAPTERS
    assert adapters.ADAPTERS["upload-focus"].source_type == "upload-focus"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_upload_source.py -v`
Expected: FAIL — `AttributeError: module 'connectors.adapters' has no attribute 'UPLOAD_ROOT'`.

- [ ] **Step 3: Implement UploadSource**

In `connectors/adapters.py`, after the `_local_export` helper add:

```python
UPLOAD_ROOT = os.path.join(ROOT, "out", "uploads")


def inbox_dir(source_id: str) -> str:
    """Per-source upload inbox. The upload endpoint writes validated files here;
    UploadSource.discover() lists them. Created on demand."""
    d = os.path.join(UPLOAD_ROOT, source_id)
    os.makedirs(d, exist_ok=True)
    return d


class UploadSource:
    """A source whose exports arrive by user upload, not cloud fetch. discover()
    lists *.csv in the source's inbox newer than the watermark; normalize() is
    the same native-FOCUS mapping every other source uses — the only difference
    from a future S3 source is WHERE the bytes came from."""
    source_type = "upload-focus"

    def discover(self, cfg: SourceConfig) -> list[DiscoveredExport]:
        d = inbox_dir(cfg.source_id)
        wm_path = os.path.join(d, ".watermark")
        watermark = os.path.getmtime(wm_path) if os.path.exists(wm_path) else 0.0
        found = []
        for name in sorted(os.listdir(d)):
            if not name.endswith(".csv"):
                continue
            p = os.path.join(d, name)
            if os.path.getmtime(p) <= watermark:
                continue  # already ingested in a prior run
            found.append(DiscoveredExport(source_id=cfg.source_id,
                                          export_id=name, uri=p))
        return found

    def advance_watermark(self, cfg: SourceConfig) -> None:
        """Touch the watermark so already-seen files aren't re-ingested."""
        d = inbox_dir(cfg.source_id)
        open(os.path.join(d, ".watermark"), "w").close()

    def normalize(self, cfg: SourceConfig, export: DiscoveredExport) -> NormalizeResult:
        rows, report = focus_native_to_focus.normalize_csv(export.uri)
        for r in rows:
            r["_source"] = "upload"
        return NormalizeResult(focus_rows=rows, report=report)
```

Then add `UploadSource()` to the `ADAPTERS` comprehension tuple (in the native-FOCUS group):

```python
    for a in (
        # native-FOCUS (current/production path)
        AwsFocusExportAdapter(), AzureFocusExportAdapter(), OciFocusExportAdapter(),
        # user-upload path (real ingestion for the MVP)
        UploadSource(),
        # provider-native billing formats (historical path)
        AwsCurAdapter(), AzureExportAdapter(), OciUsageAdapter(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_upload_source.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add connectors/adapters.py tests/test_upload_source.py
git commit -m "feat(connectors): UploadSource adapter with inbox discover + watermark"
```

---

### Task 7: Upload validation — FOCUS header check (reject early)

**Files:**
- Create: `connectors/upload_validate.py`
- Test: `tests/test_upload_validate.py` (create)

**Interfaces:**
- Consumes: `normalizer.focus_spec` (mandatory columns).
- Produces: `upload_validate.validate_focus_csv(raw: bytes) -> tuple[bool, str]` — returns `(True, "")` if the bytes parse as CSV with a header containing the FOCUS mandatory columns and at least one data row; else `(False, reason)`. Mandatory columns checked: `{"ServiceCategory","BillingCurrency","BilledCost","ChargePeriodStart","ServiceProviderName"}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_upload_validate.py`:

```python
"""Pure-logic tests for upload-time FOCUS validation (reject early)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors import upload_validate as v


HEADER = "ServiceCategory,BillingCurrency,BilledCost,ChargePeriodStart,ServiceProviderName"


def test_accepts_conformant_csv():
    raw = (HEADER + "\nCompute,USD,1.23,2026-06-01T00:00:00+00:00,AWS\n").encode()
    ok, reason = v.validate_focus_csv(raw)
    assert ok and reason == ""


def test_rejects_missing_mandatory_column():
    raw = b"ServiceCategory,BillingCurrency\nCompute,USD\n"
    ok, reason = v.validate_focus_csv(raw)
    assert not ok and "BilledCost" in reason


def test_rejects_empty_file():
    ok, reason = v.validate_focus_csv(b"")
    assert not ok and "empty" in reason.lower()


def test_rejects_header_only():
    ok, reason = v.validate_focus_csv((HEADER + "\n").encode())
    assert not ok and "no data" in reason.lower()


def test_rejects_non_csv_binary():
    ok, reason = v.validate_focus_csv(b"\x00\x01\x02not a csv")
    assert not ok
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_upload_validate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'connectors.upload_validate'`.

- [ ] **Step 3: Implement the validator**

Create `connectors/upload_validate.py`:

```python
"""Upload-time FOCUS validation — reject garbage at the door, before it ever
enters the inbox or the pipeline. A file that fails here is never written.

This is deliberately a HEADER + non-empty check, not full row conformance:
row-level conformance is the normalizer's job (it reports + drops bad rows),
and the post-load conformance validator is the authoritative gate. The point
here is to fail fast on 'this isn't a FOCUS export at all'."""
from __future__ import annotations

import csv
import io

# The minimal FOCUS columns a credible export must declare. Subset of
# focus_spec.FOCUS_COLUMNS_V1_3 — the mandatory ones our pipeline depends on.
MANDATORY = ["ServiceCategory", "BillingCurrency", "BilledCost",
             "ChargePeriodStart", "ServiceProviderName"]


def validate_focus_csv(raw: bytes) -> tuple[bool, str]:
    if not raw or not raw.strip():
        return False, "file is empty"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False, "file is not UTF-8 text (not a CSV export)"
    try:
        reader = csv.reader(io.StringIO(text))
        header = next(reader, None)
    except csv.Error as e:
        return False, f"not parseable as CSV: {e}"
    if not header:
        return False, "no header row found"
    cols = {c.strip() for c in header}
    missing = [c for c in MANDATORY if c not in cols]
    if missing:
        return False, f"missing required FOCUS column(s): {', '.join(missing)}"
    if next(reader, None) is None:
        return False, "header present but no data rows"
    return True, ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_upload_validate.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add connectors/upload_validate.py tests/test_upload_validate.py
git commit -m "feat(connectors): upload-time FOCUS header validation (reject early)"
```

---

### Task 8: Dispatcher fail-soft + structured non-conformance error

**Files:**
- Modify: `connectors/dispatcher.py`
- Test: `tests/test_dispatcher_failsoft.py` (create)

**Interfaces:**
- Consumes: `connectors.registry`, `connectors.adapters.ADAPTERS`.
- Produces: `dispatcher.run()` no longer calls `sys.exit(1)` on non-conformant categories; instead it returns `{"sources": [...], "focus_rows": int, "nonconformant_categories": [...]}`. A source whose `normalize()` raises is recorded as `{"source_id":..., "status":"error", "error": str}` and does not abort the run.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dispatcher_failsoft.py`:

```python
"""The dispatcher must not let one bad source sink the run, and must not
sys.exit on non-conformant data (an upload needs a returnable error)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors import dispatcher, registry, adapters
from connectors.contract import SourceConfig, DiscoveredExport, NormalizeResult


class _PoisonAdapter:
    source_type = "poison-test"

    def discover(self, cfg):
        return [DiscoveredExport(source_id=cfg.source_id, export_id="x", uri="x")]

    def normalize(self, cfg, export):
        raise RuntimeError("boom")


def test_one_bad_source_does_not_abort(monkeypatch, tmp_path):
    # registry with one poison source + the existing aws demo source
    monkeypatch.setattr(adapters.ADAPTERS, "get",
                        lambda t, d=None: _PoisonAdapter() if t == "poison-test"
                        else adapters.ADAPTERS.__class__.get(adapters.ADAPTERS, t, d))
    sources = [
        SourceConfig("poison-1", "poison-test", "poison", "x", "demo", "manual"),
    ]
    monkeypatch.setattr(registry, "load", lambda: sources)
    result = dispatcher.run()
    statuses = {s["source_id"]: s["status"] for s in result["sources"]}
    assert statuses["poison-1"] == "error"
    # the run completed and returned a dict (no SystemExit)
    assert "focus_rows" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_dispatcher_failsoft.py -v`
Expected: FAIL — the unguarded `adapter.normalize(...)` raises `RuntimeError` out of `run()`.

- [ ] **Step 3: Make the dispatcher fail-soft**

In `connectors/dispatcher.py`, wrap the per-export normalize loop in try/except, and replace the terminal `sys.exit(1)` with a structured return. Change the export loop body (lines ~55-65) to:

```python
        src_rows = 0
        try:
            for exp in exports:
                result = adapter.normalize(cfg, exp)
                for r in result.focus_rows:
                    r["_source_id"] = cfg.source_id
                all_rows.extend(result.focus_rows)
                all_report.extend(result.report)
                src_rows += result.loaded
        except Exception as e:  # one poison source must not sink the run
            print(f"[dispatch] {cfg.source_id}: normalize error: {e}")
            summary.append({"source_id": cfg.source_id, "status": "error",
                            "error": str(e)})
            continue
        print(f"[dispatch] {cfg.source_id:18s} [{cfg.source_type:13s}] "
              f"-> {src_rows} FOCUS rows")
        summary.append({"source_id": cfg.source_id, "status": "ok", "rows": src_rows})
```

And replace the final block (lines ~84-90) that does `sys.exit(1)` with:

```python
    print(f"[dispatch] wrote {len(all_rows)} FOCUS rows -> {FOCUS_CSV}")
    print(f"[dispatch] distinct ServiceCategory: {distinct}")
    if invalid:
        print(f"[dispatch] !!! non-conformant categories: {invalid}")

    return {"sources": summary, "focus_rows": len(all_rows),
            "nonconformant_categories": invalid}
```

(The normalizer already drops fatal rows, so `invalid` should normally be empty; surfacing it without exiting lets the upload endpoint show it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_dispatcher_failsoft.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add connectors/dispatcher.py tests/test_dispatcher_failsoft.py
git commit -m "fix(connectors): dispatcher fail-soft + structured non-conformance (no sys.exit)"
```

---

### Task 9: Stubbed API-pull adapters (the deferred contract surface)

**Files:**
- Create: `connectors/api_pull.py`
- Modify: `connectors/adapters.py` (register the stubs)
- Test: `tests/test_api_pull_stub.py` (create)

**Interfaces:**
- Consumes: `connectors.contract`.
- Produces: `api_pull.AwsCostExplorerSource` (`source_type="aws-api-pull"`) and `api_pull.AzureExportSource` (`source_type="azure-api-pull"`). Both: `discover()`/`normalize()` raise `NotImplementedError("deferred to a later release")`. A module-level `API_PULL_TYPES = {"aws-api-pull","azure-api-pull"}` marks which registered types are not-yet-live (the UI greys these out). Registered in `ADAPTERS`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_pull_stub.py`:

```python
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors import api_pull, adapters
from connectors.contract import SourceConfig


def _cfg(t):
    return SourceConfig("s", t, "s", "loc", "demo", "manual")


def test_api_pull_sources_are_stubbed():
    for cls, t in ((api_pull.AwsCostExplorerSource, "aws-api-pull"),
                   (api_pull.AzureExportSource, "azure-api-pull")):
        src = cls()
        assert src.source_type == t
        with pytest.raises(NotImplementedError):
            src.discover(_cfg(t))


def test_api_pull_types_marked_and_registered():
    assert api_pull.API_PULL_TYPES == {"aws-api-pull", "azure-api-pull"}
    for t in api_pull.API_PULL_TYPES:
        assert t in adapters.ADAPTERS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_api_pull_stub.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'connectors.api_pull'`.

- [ ] **Step 3: Implement the stubs**

Create `connectors/api_pull.py`:

```python
"""Deferred API-pull connectors — the SECOND implementation behind the same
SourceAdapter contract, defined now so the extension surface is concrete.

These are intentionally NOT implemented: a live pull needs real cloud
credentials, which this synthetic-only build never holds, and there is nothing
to verify against on synthetic data. The MVP ingestion path is upload
(UploadSource). When a credentialed deployment wants automated pull, fill in
discover()/normalize() here against:
  - AWS:   Data Exports (FOCUS) delivered to S3 → list new objects under a
           prefix; normalize() reuses focus_native_to_focus.
  - Azure: Cost Management FOCUS export to a storage container → same shape.
The dispatcher already treats a raised error as a per-source 'error' status
(fail-soft), so a registered-but-unfilled source never breaks a run."""
from __future__ import annotations

from connectors.contract import DiscoveredExport, NormalizeResult, SourceConfig

_MSG = "API-pull connector deferred to a later release — use upload for now"


class _StubApiPull:
    source_type = ""

    def discover(self, cfg: SourceConfig) -> list[DiscoveredExport]:
        raise NotImplementedError(_MSG)

    def normalize(self, cfg: SourceConfig, export: DiscoveredExport) -> NormalizeResult:
        raise NotImplementedError(_MSG)


class AwsCostExplorerSource(_StubApiPull):
    source_type = "aws-api-pull"


class AzureExportSource(_StubApiPull):
    source_type = "azure-api-pull"


# Types that are registered but NOT yet live. The Connect UI renders these
# disabled with a 'later release' label so a stub never looks operational.
API_PULL_TYPES = {"aws-api-pull", "azure-api-pull"}
```

In `connectors/adapters.py`, import and register them. Add near the top imports:

```python
from connectors.api_pull import AwsCostExplorerSource, AzureExportSource
```

And add to the `ADAPTERS` tuple:

```python
        # API-pull (deferred; registered-but-stubbed — see api_pull.py)
        AwsCostExplorerSource(), AzureExportSource(),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_api_pull_stub.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add connectors/api_pull.py connectors/adapters.py tests/test_api_pull_stub.py
git commit -m "feat(connectors): register stubbed API-pull adapters (deferred contract surface)"
```

---

### Task 10: Upload endpoint + Connect UI (file picker, honesty, disabled stubs)

**Files:**
- Modify: `connectors/router.py`
- Modify: `web/templates/view_connect.html`
- Test: `tests/test_connect_upload_route.py` (create)

**Interfaces:**
- Consumes: `connectors.upload_validate.validate_focus_csv` (Task 7), `connectors.adapters.UploadSource`/`inbox_dir` (Task 6), `connectors.adapters.ADAPTERS`, `connectors.api_pull.API_PULL_TYPES` (Task 9), `connectors.registry`, `connectors.dispatcher`.
- Produces: `POST /connect/upload` (multipart: `source_id`, `file`) → validates, writes to inbox, registers an `upload-focus` source if new, runs the dispatcher, advances the watermark; returns `{"ok":bool, ...}`. The Connect page renders a file-upload form and greys out `API_PULL_TYPES` in the type dropdown. Uses FastAPI `UploadFile`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_connect_upload_route.py`:

```python
"""Route test for the upload endpoint using FastAPI's TestClient."""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from web.app import app

client = TestClient(app)

HEADER = ("ServiceCategory,BillingCurrency,BilledCost,ChargePeriodStart,"
          "ServiceProviderName,ResourceId\n")


def test_upload_rejects_non_focus():
    bad = io.BytesIO(b"not,a,focus\n1,2,3\n")
    r = client.post("/connect/upload",
                    data={"source_id": "test-upload-bad"},
                    files={"file": ("bad.csv", bad, "text/csv")})
    assert r.status_code == 400
    assert "missing required FOCUS column" in r.json()["error"]


def test_upload_accepts_focus(tmp_path, monkeypatch):
    from connectors import adapters
    monkeypatch.setattr(adapters, "UPLOAD_ROOT", str(tmp_path))
    good = io.BytesIO((HEADER + "Compute,USD,1.5,2026-06-01T00:00:00+00:00,AWS,i-demo\n").encode())
    r = client.post("/connect/upload",
                    data={"source_id": "test-upload-ok"},
                    files={"file": ("good.csv", good, "text/csv")})
    assert r.status_code == 200
    assert r.json()["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_connect_upload_route.py -v`
Expected: FAIL — 404 / no `/connect/upload` route.

- [ ] **Step 3: Implement the endpoint**

In `connectors/router.py`, add imports at the top:

```python
from fastapi import APIRouter, Request, UploadFile, File, Form

from connectors import upload_validate
from connectors.adapters import UploadSource, inbox_dir
from connectors.api_pull import API_PULL_TYPES
```

Add the endpoint (after `connect_add`):

```python
@router.post("/upload")
async def connect_upload(source_id: str = Form(...), file: UploadFile = File(...)):
    """Real upload ingestion: validate FOCUS-conformance BEFORE accepting, write
    to the source's inbox, register an upload source if new, run the dispatcher.
    A file that fails validation is never written and never ingested."""
    sid = (source_id or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "source_id is required"}, status_code=400)

    raw = await file.read()
    ok, reason = upload_validate.validate_focus_csv(raw)
    if not ok:
        return JSONResponse({"ok": False, "error": reason}, status_code=400)

    # Register an upload source for this id if it doesn't exist yet.
    existing = {s.source_id for s in registry.load()}
    if sid not in existing:
        registry.add_source(SourceConfig(
            source_id=sid, source_type="upload-focus",
            display_name=f"Upload — {sid}", location=f"out/uploads/{sid}",
            credential_ref="upload:no-credential", schedule="manual"))

    # Write the validated bytes into the inbox.
    d = inbox_dir(sid)
    dest = os.path.join(d, os.path.basename(file.filename or "upload.csv"))
    with open(dest, "wb") as f:
        f.write(raw)

    result = dispatcher.run()
    UploadSource().advance_watermark(
        SourceConfig(sid, "upload-focus", sid, d, "upload", "manual"))
    return {"ok": True, "dispatch": result, "sources": _sources_view()}
```

In `connect_index`, pass the stub types to the template:

```python
    return templates.TemplateResponse(
        request, "view_connect.html",
        {
            "active": "connect",
            "sources": _sources_view(),
            "types": sorted(ADAPTERS.keys()),
            "api_pull_types": sorted(API_PULL_TYPES),
            "demo_locations": DEMO_LOCATIONS,
        },
    )
```

- [ ] **Step 4: Add the upload form + honesty banner + disabled stubs to the template**

In `web/templates/view_connect.html`, replace the PoC honesty banner text (lines 25-36) with an accurate one:

```html
<div class="banner" role="note">
  <div>
    <div class="label">Connect &amp; run · ingestion</div>
    <div class="text">
      <strong>Upload a FOCUS export</strong> (below) and it is validated as FOCUS-conformant,
      normalized, loaded and joined — the real ingestion path, exercised on
      <strong>synthetic</strong> data here. Registered generator sources remain as demo seeds.
      <strong>API-pull connectors are shown but disabled</strong> — they ship in a later release;
      a credentialed deployment fills them in behind the same contract. Credentials are stored
      as reference strings only, never inline.
    </div>
  </div>
</div>
```

Add an upload section after the "Add a source" form (after line 79's `</form>`):

```html
<div class="section-label"><span class="num">03.</span>Upload a FOCUS export</div>
<form id="upf" enctype="multipart/form-data">
  <div class="grid2">
    <label>Source ID<input id="u-id" placeholder="e.g. upload-aws-june" autocomplete="off"></label>
    <label>FOCUS export (.csv)<input id="u-file" type="file" accept=".csv"></label>
  </div>
  <button id="upb" type="submit" class="askbtn">Validate &amp; ingest</button>
</form>
<div id="upload-result" style="margin-top:16px;"></div>

<script>
  document.getElementById('upf').addEventListener('submit', async e => {
    e.preventDefault();
    const sid = document.getElementById('u-id').value.trim();
    const fileEl = document.getElementById('u-file');
    const el = document.getElementById('upload-result');
    if (!sid || !fileEl.files.length) { el.innerHTML = banner('Error', 'Source ID and a file are required.'); return; }
    const fd = new FormData();
    fd.append('source_id', sid);
    fd.append('file', fileEl.files[0]);
    el.innerHTML = '<p style="color:var(--ink-mid)">Validating &amp; ingesting…</p>';
    const r = await fetch('/connect/upload', {method:'POST', body:fd});
    const data = await r.json();
    if (!r.ok) { el.innerHTML = banner('Rejected', data.error || 'Upload failed'); return; }
    el.innerHTML = banner('Ingested',
      'Uploaded export validated and ingested. Dispatcher produced <strong>'+data.dispatch.focus_rows+'</strong> FOCUS rows. Reloading…');
    setTimeout(() => location.reload(), 1200);
  });
</script>
```

Grey out the stub types in the type `<select>` (replace line 71's option loop):

```html
      <select id="f-type">{% for t in types %}<option value="{{ t }}"{% if t in api_pull_types %} disabled{% endif %}>{{ t }}{% if t in api_pull_types %} — later release{% endif %}</option>{% endfor %}</select>
```

- [ ] **Step 5: Run the route test**

Run: `.venv/bin/python -m pytest tests/test_connect_upload_route.py -v`
Expected: PASS (2 tests). If `fastapi.testclient` needs `httpx`, it is already a FastAPI dep; if missing, `pip install httpx` into `.venv` (dev-only).

- [ ] **Step 6: Rebuild the web image and visually verify (templates are baked in — W-3)**

Run: `docker compose build web && docker compose up -d web` then load `http://localhost:8000/connect/` and confirm the upload form renders and the API-pull types show disabled. Route 200 ≠ renders correctly (W-12) — eyeball it.

- [ ] **Step 7: Commit**

```bash
git add connectors/router.py web/templates/view_connect.html tests/test_connect_upload_route.py
git commit -m "feat(connect): real upload endpoint + UI, honest banner, disabled API-pull stubs"
```

---

### Task 11: Connector SDK contract doc + GOTCHAS entries

**Files:**
- Create: `connectors/README.md`
- Modify: `GOTCHAS.md`

**Interfaces:**
- Consumes: nothing (documentation).
- Produces: the reusability surface doc — what `SourceAdapter.discover()`/`normalize()` must do, what each `SourceConfig` field means, how to add a new source type (worked example), and the upload vs API-pull distinction.

- [ ] **Step 1: Write the SDK contract doc**

Create `connectors/README.md` documenting, with a worked example, how a customer authors a new adapter. Must include: the `SourceConfig`/`DiscoveredExport`/`NormalizeResult` field reference (copied accurately from `connectors/contract.py`); the rule that `normalize()` must return FOCUS v1.3 rows keyed by `FOCUS_COLUMNS_V1_3` display names and set `_source`; that `discover()` must be idempotent (watermark pattern, as `UploadSource` shows); registration in `adapters.ADAPTERS`; and that a raised error is caught fail-soft by the dispatcher. Show a complete minimal example adapter (~20 lines) reading a local CSV, mirroring `UploadSource`.

- [ ] **Step 2: Add GOTCHAS entries**

Append to `GOTCHAS.md` the non-obvious findings from this slice (use the next free IDs in that file's scheme; one entry each):
  - The dispatcher used to `sys.exit(1)` on non-conformant categories *after* writing the CSV — fine for a batch run, fatal for an interactive upload; now returns structured `nonconformant_categories`.
  - Upload validation is header-only by design (fast reject); row conformance stays the normalizer's job and post-load `focus_conformance()` is the authoritative gate — three layers, each fails safe.
  - `FOCUS_GEN_SCALE` multiplies per-day volume but the controlled dirty rows are *not* scaled (kept at one each) so validator drop counts stay assertable.
  - Azure commitment cost spread must be computed in AED (to match `BilledCost`/`BillingCurrency=AED`), not USD, or the currency column lies.

- [ ] **Step 3: Verify the doc references real symbols**

Run: `grep -n "discover\|normalize\|SourceConfig\|ADAPTERS\|_source" connectors/README.md`
Expected: matches present — confirm names match `connectors/contract.py` and `connectors/adapters.py` exactly.

- [ ] **Step 4: Commit**

```bash
git add connectors/README.md GOTCHAS.md
git commit -m "docs(connectors): SDK contract guide + GOTCHAS for upload ingestion slice"
```

---

### Task 12: Full verification — seed at volume, integrity at scale, /reseed

**Files:**
- Modify: `tests/test_data_integrity.py` (add scale-aware assertions)
- Modify: `docker-compose.yml` (set `FOCUS_GEN_SCALE` default for the demo seed) — optional, see Step 4.

**Interfaces:**
- Consumes: everything above, end to end.
- Produces: confidence that the B-6/B-7 currency + join guards hold at the larger volume, conformance passes, and all routes answer.

- [ ] **Step 1: Add a charge-category integrity test**

In `tests/test_data_integrity.py`, add:

```python
def test_charge_category_mix_loaded():
    """Realistic data carries more than Usage — Tax/Purchase/Credit/Refund
    must survive ingestion into focus_costs."""
    db = _db_or_skip()
    cats = {r["c"] for r in db.query(
        "SELECT DISTINCT charge_category AS c FROM focus_costs "
        "WHERE charge_category IS NOT NULL")}
    assert {"Usage", "Tax", "Purchase"} <= cats, f"charge categories loaded: {cats}"


def test_commitment_rows_have_effective_below_billed():
    """Commitment coverage means EffectiveCost < BilledCost for covered rows
    (in USD, so the comparison is currency-safe)."""
    db = _db_or_skip()
    n = db.query("""
        SELECT COUNT(*) AS n FROM focus_costs
        WHERE commitment_discount_id IS NOT NULL
          AND commitment_discount_id <> ''
          AND effective_cost < billed_cost""")[0]["n"]
    assert n > 0, "expected commitment-covered rows with EffectiveCost < BilledCost"
```

- [ ] **Step 2: Seed at volume and run the full suite**

Run (compose stack up):

```bash
docker compose build web && docker compose up -d web
docker compose exec -T -e FOCUS_PG_HOST=db -e FOCUS_PG_PASS=focus_app_demo -e FOCUS_GEN_SCALE=10 web python -m docker.seed
FOCUS_PG_HOST=127.0.0.1 FOCUS_PG_PASS=focus_app_demo .venv/bin/python -m pytest tests/ -q
```

Expected: `[seed] complete` with focus_costs row count in the thousands; **all** pytest pass (logic + integrity), no skips on the host (DB published to 127.0.0.1 per P-11).

- [ ] **Step 3: Conformance + join reconcile at volume**

Run:

```bash
docker compose exec -T -e FOCUS_PG_HOST=db -e FOCUS_PG_PASS=focus_app_demo web python -c "
from web import queries as q
c=q.focus_conformance(); print('conformant=', c['conformant'], c['rules_passed'],'/',c['rules_total'])
d=q.join_distribution(); print('join rows:', sum(r.get('n', r.get('count',0)) for r in d) if d else 0)
"
```

Expected: `conformant= True`, all rules pass, join distribution non-empty (matched + the J-6 unmatched residual present).

- [ ] **Step 4: Set the demo seed scale (optional but recommended)**

In `docker-compose.yml`, under the `web` service `environment:`, add a default so the container seed produces credible volume without a manual env:

```yaml
      FOCUS_GEN_SCALE: ${FOCUS_GEN_SCALE:-8}
```

Rebuild + re-seed (Step 2 commands without the inline `-e FOCUS_GEN_SCALE`). Confirm volume.

- [ ] **Step 5: Run the /reseed skill end to end**

Invoke `/reseed` and confirm every step green: rebuild → seed → pytest → conformance `conformant=True` → `ALL 200: True` across all routes (including `/connect/`).

- [ ] **Step 6: Commit**

```bash
git add tests/test_data_integrity.py docker-compose.yml
git commit -m "test+chore: integrity assertions at volume + demo seed scale default"
```

---

## Self-Review

**Spec coverage:**
- §1.1 realistic synthetic exports → Tasks 4, 5 ✓
- §1.2 real upload ingestion path → Tasks 6, 7, 10 ✓
- §1.3 config-driven onboarding, zero code change → Task 10 (upload auto-registers an `upload-focus` source; existing registry/dispatcher unchanged) ✓
- §1.4 documented connector SDK contract → Task 11 ✓
- §2 Delta 1 real `discover()` → Task 6; Delta 2 upload endpoint → Task 10; Delta 3 generators → Tasks 4-5; Addition stubbed API-pull → Task 9 ✓
- §3 six realism dimensions: volume/breadth (4-5), charge-category mix (1,2,5), commitments (1,3,4,5), tag sparsity (4,5), currency realism (5), controlled dirty rows (5) ✓
- §4 three validation layers (upload Task 7/10, row-level Task 2, post-load conformance Task 12) + fail-soft + structured error (Task 8) + honesty surfaces (Task 10) ✓
- §5 tests + DoD: upload validation (7), generator realism (5), dispatcher fail-soft (8), data-integrity at volume (12), /reseed (12), SDK doc (11), GOTCHAS (11) ✓

**Placeholder scan:** No TBD/TODO; every code step carries concrete code. Task 5 step (a) explicitly notes "no edit needed" with the reason rather than leaving it vague.

**Type consistency:** `validate_focus_csv(bytes)->(bool,str)`, `UploadSource.source_type="upload-focus"`, `inbox_dir(str)->str`, `API_PULL_TYPES` set, `dispatcher.run()->dict` with `sources`/`focus_rows`/`nonconformant_categories` — all referenced consistently across Tasks 6-12. Loader column names (`commitment_discount_id`/`_status`) match schema (Task 3) and the integrity test (Task 12).

**One known risk to watch during execution:** Task 8's monkeypatch of `ADAPTERS.get` is awkward; if it proves brittle, simplify the test to register the poison adapter directly into `adapters.ADAPTERS["poison-test"]` in the test and pop it in teardown. The behavior under test (fail-soft) is what matters.
