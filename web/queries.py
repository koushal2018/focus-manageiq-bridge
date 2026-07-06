"""Read-only SQL the web layer runs.

All queries here are deterministic and parameterized. The Bedrock NL-query
slice (slice 7) will eventually augment this with text-to-SQL --- but per
SPEC §3.6, the canned queries come FIRST and must work without the AI
layer at all.
"""
from __future__ import annotations

from web import db


# --- View 1: AI cost by cloud x model ---
# Requirement #1 from SPEC §1. FOCUS-native: ServiceCategory='AI and
# Machine Learning' is a normative allowed value (verified F-2). The
# Bedrock model id was pushed into sku_meter by aws_to_focus.py so we
# can group on it; for Azure OpenAI the meter id is the Azure MeterName.
def ai_cost_by_model() -> list[dict]:
    # SUM the USD-normalized column (H-1) — never raw billed_cost, which
    # mixes AED and USD across providers.
    sql = """
        SELECT  service_provider_name,
                sku_meter,
                service_name,
                COUNT(*)             AS row_count,
                SUM(billed_cost_usd) AS total_cost,
                'USD'                AS billing_currency
        FROM    focus_costs
        WHERE   service_category = 'AI and Machine Learning'
        GROUP BY service_provider_name, sku_meter, service_name
        ORDER BY total_cost DESC
    """
    return db.query(sql)


# --- View 2: Utilization x cost ---
# Requirement #2: utilization % is NOT in FOCUS; it comes from MIQ
# (GOTCHA J-2). We join via resource_join_map only for status='matched'
# rows, then group by VM.
def utilization_x_cost() -> list[dict]:
    sql = """
        SELECT  j.miq_vm_id,
                j.miq_vm_name,
                j.miq_vendor,
                j.focus_source,
                j.focus_billed_cost_sum::NUMERIC(12,2) AS cost,
                ROUND(AVG(u.cpu_usage_pct)::NUMERIC, 2) AS avg_cpu_pct,
                ROUND(AVG(u.mem_usage_pct)::NUMERIC, 2) AS avg_mem_pct,
                COUNT(u.timestamp) AS rollup_samples
        FROM    resource_join_map j
        LEFT JOIN miq_utilization u
               ON  u.miq_vm_id  = j.miq_vm_id::BIGINT
              AND j.status = 'matched'
        WHERE   j.status = 'matched'
        GROUP BY j.miq_vm_id, j.miq_vm_name, j.miq_vendor, j.focus_source, j.focus_billed_cost_sum
        ORDER BY j.focus_billed_cost_sum DESC
    """
    return db.query(sql)


# --- View 3: Cloud vs on-prem cost ---
# Requirement #3. Cloud half = focus_costs grouped by provider. On-prem
# half = focus.miq_onprem_cost, populated by onprem/cost_model.py (slice
# 6; replaces the original "wire to MIQ chargeback module" plan per O-1).


def cloud_cost_by_provider() -> list[dict]:
    # USD-normalized SUM (H-1). billing_currency shows the SOURCE currency
    # for transparency, but the total is always USD.
    sql = """
        SELECT  service_provider_name,
                source,
                COUNT(*)                              AS row_count,
                SUM(billed_cost_usd)::NUMERIC(12,2)   AS total_cost,
                MAX(billing_currency)                 AS source_currency,
                'USD'                                 AS billing_currency
        FROM    focus_costs
        GROUP BY service_provider_name, source
        ORDER BY total_cost DESC
    """
    return db.query(sql)


def onprem_cost_estimate() -> list[dict]:
    """Read persisted on-prem recharge rows from focus.miq_onprem_cost.

    Per GOTCHA O-1: the rows here come from `onprem/cost_model.py`,
    not from a live ManageIQ chargeback module (that path died with the
    appliance per LM-1). The cost model uses ENBD's existing per-resource
    formula; the EBA team replaces the rate constants with real numbers.

    We enrich each row with workload metadata (cpu_cores, memory_mb,
    util %) by joining to the generators/common.WORKLOADS table in
    Python --- the appliance is gone so there's nowhere else to look.
    """
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from generators import common as gen_common

    by_name = {w.canonical_name: w for w in gen_common.WORKLOADS}
    persisted = db.query("""
        SELECT miq_vm_id,
               sub_account_id,
               billed_cost::NUMERIC(12,2) AS monthly_cost_usd,
               billing_currency,
               charge_period_start,
               charge_period_end,
               notes
        FROM   miq_onprem_cost
        ORDER  BY billed_cost DESC
    """)

    # Map miq_vm_id back to a canonical workload via the canonical id map
    # (GOTCHA H-2 — single source of truth, no re-derived counter).
    id_to_workload: dict[int, gen_common.Workload] = {}
    _id_map = gen_common.workload_vm_ids()
    _by_name = {w.canonical_name: w for w in gen_common.WORKLOADS}
    for name, ids in _id_map.items():
        for vid in ids:
            id_to_workload[vid] = _by_name[name]

    out = []
    for r in persisted:
        wl = id_to_workload.get(int(r["miq_vm_id"]))
        if wl is None:
            continue
        gb = wl.memory_mb / 1024.0
        out.append({
            "miq_vm_name":   wl.canonical_name,
            "business_unit": wl.business_unit,
            "cpu_cores":     wl.cpu_cores,
            "memory_gb":     round(gb, 1),
            "monthly_cost_usd": float(r["monthly_cost_usd"]),
            "billing_currency": r["billing_currency"],
            "avg_cpu_pct":   wl.cpu_pct,
            "avg_mem_pct":   wl.mem_pct,
            "period":        f'{r["charge_period_start"].date()} → {r["charge_period_end"].date()}',
            "rate_notes":    r["notes"],
        })
    return out


# --- View 4: Carbon stub feed ---
# Requirement #4. FOCUS has no carbon column through v1.4. This is a
# layout placeholder for slice 8; numbers are intentionally fake.
def carbon_stub() -> list[dict]:
    return [
        {"provider": "AWS",         "feed": "AWS Customer Carbon Footprint Tool (CCFT)",       "status": "real, available via Billing Console; not yet wired"},
        {"provider": "Azure",       "feed": "Azure Emissions Impact Dashboard",                "status": "real, available via Azure Portal; not yet wired"},
        {"provider": "OCI",         "feed": "No first-party feed today",                       "status": "would need custom model"},
        {"provider": "On-prem (MIQ)","feed": "Custom model (kWh per VM * grid intensity)",     "status": "would need ENBD DC PUE + grid data"},
    ]


def carbon_intensity_placeholder() -> list[dict]:
    """Total cost per provider with a fake $/tonne CO2e ratio --- LAYOUT
    only. Demonstrates how a carbon view would lay out; the numbers are
    NOT real and the banner says so.
    """
    rows = cloud_cost_by_provider()
    # Use deliberately implausible ratios so no one mistakes for real
    fake_ratios_t_per_aed = {  # tonnes CO2e per AED 1000
        "AWS":                          0.42,
        "Microsoft":                    0.51,
        "Oracle Cloud Infrastructure":  0.55,
    }
    for r in rows:
        ratio = fake_ratios_t_per_aed.get(r["service_provider_name"], 0.5)
        cost = float(r["total_cost"] or 0)
        r["fake_co2e_tonnes"] = round(cost * ratio / 1000.0, 3)
        r["currency"] = r.get("billing_currency", "")
    return rows


# --- Top-of-page banner stats ---
def headline_stats() -> dict:
    """Counts the dashboard shows at the top --- a one-pass sanity check."""
    out: dict = {}
    out["focus_rows"] = db.query("SELECT COUNT(*) AS n FROM focus_costs")[0]["n"]
    out["join_rows"]  = db.query("SELECT COUNT(*) AS n FROM resource_join_map")[0]["n"]
    out["util_rows"]  = db.query("SELECT COUNT(*) AS n FROM miq_utilization")[0]["n"]
    out["join_status"] = db.query(
        "SELECT status, COUNT(*) AS n FROM resource_join_map GROUP BY status ORDER BY status"
    )
    return out


# =====================================================================
# Reference-console dashboard queries (designed UI, real data)
# =====================================================================

def headline_kpis() -> dict:
    """The 4 KPI tiles: total billed (USD), focus rows, % joined, rightsizing."""
    total = db.query("SELECT COALESCE(SUM(billed_cost_usd),0) AS t FROM focus_costs")[0]["t"]
    onprem = db.query("SELECT COALESCE(SUM(billed_cost),0) AS t FROM miq_onprem_cost")[0]["t"]
    focus_rows = db.query("SELECT COUNT(*) AS n FROM focus_costs")[0]["n"]
    status = {r["status"]: r["n"] for r in db.query(
        "SELECT status, COUNT(*) AS n FROM resource_join_map GROUP BY status")}
    matched = status.get("matched", 0)
    total_join = sum(status.values()) or 1
    pct_joined = round(100.0 * matched / total_join, 1)
    # rightsizing candidates: matched workloads with avg CPU < 25 and real cost
    rightsize = db.query("""
        SELECT COUNT(*) AS n FROM (
          SELECT j.miq_vm_id
          FROM resource_join_map j JOIN miq_utilization u ON u.miq_vm_id = j.miq_vm_id::BIGINT
          WHERE j.status='matched'
          GROUP BY j.miq_vm_id, j.focus_billed_cost_sum
          HAVING AVG(u.cpu_usage_pct) < 25 AND j.focus_billed_cost_sum > 30
        ) q""")[0]["n"]
    addressable = db.query("""
        SELECT COALESCE(SUM(j.focus_billed_cost_sum),0) AS t FROM (
          SELECT j2.miq_vm_id, MAX(j2.focus_billed_cost_sum) focus_billed_cost_sum
          FROM resource_join_map j2 JOIN miq_utilization u ON u.miq_vm_id=j2.miq_vm_id::BIGINT
          WHERE j2.status='matched'
          GROUP BY j2.miq_vm_id HAVING AVG(u.cpu_usage_pct) < 25 AND MAX(j2.focus_billed_cost_sum) > 30
        ) j""")[0]["t"]
    return {
        "total_usd": float(total or 0) + float(onprem or 0),
        "cloud_usd": float(total or 0),
        "onprem_usd": float(onprem or 0),
        "focus_rows": focus_rows,
        "pct_joined": pct_joined,
        "matched": matched,
        "rightsize_count": rightsize,
        "rightsize_addressable": float(addressable or 0),
        "requirements_answered": 2,  # native + (utilization partial counts as answered-with-caveat)
    }


def pipeline_snapshot() -> list[dict]:
    """Row counts per source table — the 'pipeline snapshot' panel."""
    def n(t): return db.query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
    return [
        {"table": "focus_costs",       "rows": n("focus_costs"),       "status": "OK",    "cls": "v-native"},
        {"table": "resource_join_map", "rows": n("resource_join_map"), "status": "OK",    "cls": "v-native"},
        {"table": "miq_utilization",   "rows": n("miq_utilization"),   "status": "OK",    "cls": "v-native"},
        {"table": "miq_onprem_cost",   "rows": n("miq_onprem_cost"),   "status": "Model", "cls": "v-partial"},
    ]


def provider_ingest() -> list[dict]:
    """FOCUS row counts per source — the ingestion bar chart."""
    rows = db.query("""
        SELECT source AS label, COUNT(*) AS rows
        FROM focus_costs GROUP BY source ORDER BY rows DESC""")
    return [{"label": (r["label"] or "?").upper(), "rows": r["rows"]} for r in rows]


def join_distribution() -> list[dict]:
    """Join status with percentages, mapped to the reference's 4 segments.

    The FOCUS-only bucket is SPLIT (J-6): managed-service rows (AI/ML,
    storage, networking — no VM exists, so unmatched is *expected*) vs
    compute rows that should have matched (a real worklist). This stops
    "matched %" reading as a failure — see GOTCHAS J-6.
    """
    rows = db.query("SELECT status, COUNT(*) AS n FROM resource_join_map GROUP BY status")
    total = sum(r["n"] for r in rows) or 1
    by = {r["status"]: r["n"] for r in rows}

    # Within unmatched_focus_only, how many are managed services (expected)
    # vs compute (should-have-matched)? Categories with no VM representation
    # in ManageIQ are expected to be cost-only.
    EXPECTED_CATS = ("AI and Machine Learning", "Storage", "Networking",
                     "Analytics", "Databases", "Integration", "Management and Governance")
    fo = db.query("""
        SELECT focus_service_category AS cat, COUNT(*) AS n
        FROM resource_join_map WHERE status='unmatched_focus_only'
        GROUP BY focus_service_category""")
    expected = 0
    worklist = 0
    for r in fo:
        cat = (r["cat"] or "")
        # a row's category string may be " | "-joined; treat as expected if
        # ANY part is a managed-service category and none is Compute.
        parts = [p.strip() for p in cat.split("|")]
        is_compute = any(p == "Compute" for p in parts)
        is_expected = any(p in EXPECTED_CATS for p in parts) and not is_compute
        if is_expected:
            expected += r["n"]
        else:
            worklist += r["n"]

    out = []
    if by.get("matched"):
        out.append({"status": "matched", "label": "Matched · FOCUS ↔ ManageIQ",
                    "seg": "seg-1", "n": by["matched"],
                    "pct": round(100.0 * by["matched"] / total, 1)})
    if expected:
        out.append({"status": "expected", "label": "Managed services · cost-only (expected)",
                    "seg": "seg-2", "n": expected, "pct": round(100.0 * expected / total, 1)})
    if worklist:
        out.append({"status": "worklist", "label": "Compute, FOCUS-only · tagging worklist",
                    "seg": "seg-4", "n": worklist, "pct": round(100.0 * worklist / total, 1)})
    if by.get("unmatched_miq_only"):
        out.append({"status": "unmatched_miq_only", "label": "Inventory-only · no cost row",
                    "seg": "seg-3", "n": by["unmatched_miq_only"],
                    "pct": round(100.0 * by["unmatched_miq_only"] / total, 1)})
    for s in ("no_resource_id", "ambiguous"):
        if by.get(s):
            lbl = {"no_resource_id": "No resource id · tax/refund/support",
                   "ambiguous": "Ambiguous · key collision"}[s]
            out.append({"status": s, "label": lbl, "seg": "seg-4",
                        "n": by[s], "pct": round(100.0 * by[s] / total, 1)})
    return out


# Mandatory FOCUS v1.3 non-null columns and the ServiceCategory closed set,
# both derived from the single source of truth (normalizer.focus_spec) so the
# conformance dashboard can never disagree with the loader gate about what
# "valid FOCUS" means. focus_spec stores (display, db_col); this view wants
# (db_col, display), so we swap.
from normalizer.focus_spec import (
    MANDATORY_NONNULL_V1_3 as _SPEC_MANDATORY,
    SERVICE_CATEGORIES_V1_3 as _FOCUS_SERVICE_CATEGORIES,
)
_FOCUS_MANDATORY_NONNULL = [(db_col, display) for display, db_col in _SPEC_MANDATORY]


def focus_conformance() -> dict:
    """Validate the loaded focus_costs against FOCUS v1.3 normative rules.

    Not a claim — a check. Surfaces row-level conformance so leadership sees
    the data is *validated* FOCUS, not just labelled FOCUS (GOTCHAS F-2).
    Every rule reports pass/fail counts; the headline is % conformant.
    """
    total = db.query("SELECT COUNT(*) AS n FROM focus_costs")[0]["n"] or 0
    checks: list[dict] = []

    # 1. Mandatory non-null columns.
    for col, display in _FOCUS_MANDATORY_NONNULL:
        bad = db.query(f"SELECT COUNT(*) AS n FROM focus_costs WHERE {col} IS NULL")[0]["n"]
        checks.append({
            "rule": f"{display} present (non-null)",
            "kind": "mandatory-non-null",
            "fail": bad,
            "ok": bad == 0,
        })

    # 2. ServiceCategory ∈ allowed closed set.
    cats = db.query("SELECT DISTINCT service_category AS c FROM focus_costs WHERE service_category IS NOT NULL")
    bad_cats = [c["c"] for c in cats if c["c"] not in _FOCUS_SERVICE_CATEGORIES]
    bad_cat_rows = 0
    if bad_cats:
        bad_cat_rows = db.query(
            "SELECT COUNT(*) AS n FROM focus_costs WHERE service_category = ANY(%(b)s)",
            {"b": bad_cats})[0]["n"]
    checks.append({
        "rule": "ServiceCategory in FOCUS allowed values",
        "kind": "allowed-value",
        "fail": bad_cat_rows,
        "ok": bad_cat_rows == 0,
        "detail": ("offending: " + ", ".join(bad_cats)) if bad_cats else "",
    })

    # 3. USD normalization applied wherever a billing currency + cost exist
    #    (H-1 — every billable row must carry billed_cost_usd).
    bad_usd = db.query("""
        SELECT COUNT(*) AS n FROM focus_costs
        WHERE billed_cost IS NOT NULL AND billed_cost_usd IS NULL""")[0]["n"]
    checks.append({
        "rule": "USD normalization present (billed_cost_usd)",
        "kind": "currency",
        "fail": bad_usd,
        "ok": bad_usd == 0,
    })

    # 4. BillingCurrency is a 3-letter ISO-ish code (StringHandling sanity).
    bad_ccy = db.query("""
        SELECT COUNT(*) AS n FROM focus_costs
        WHERE billing_currency !~ '^[A-Z]{3}$'""")[0]["n"]
    checks.append({
        "rule": "BillingCurrency is a 3-letter code",
        "kind": "format",
        "fail": bad_ccy,
        "ok": bad_ccy == 0,
    })

    failed_rules = sum(1 for c in checks if not c["ok"])
    total_fail_rows = sum(c["fail"] for c in checks)
    return {
        "total_rows": total,
        "checks": checks,
        "rules_total": len(checks),
        "rules_passed": len(checks) - failed_rules,
        "conformant": failed_rules == 0,
        "total_fail_rows": total_fail_rows,
    }


def top_rightsizing(limit: int = 6) -> list[dict]:
    """Workloads with low CPU and real cost — the rightsizing candidates."""
    return db.query("""
        SELECT j.miq_vm_id, j.miq_vm_name, j.focus_source,
               j.focus_billed_cost_sum::NUMERIC(12,2) AS cost,
               ROUND(AVG(u.cpu_usage_pct)::NUMERIC,1) AS cpu,
               ROUND(AVG(u.mem_usage_pct)::NUMERIC,1) AS mem
        FROM resource_join_map j JOIN miq_utilization u ON u.miq_vm_id = j.miq_vm_id::BIGINT
        WHERE j.status='matched'
        GROUP BY j.miq_vm_id, j.miq_vm_name, j.focus_source, j.focus_billed_cost_sum
        ORDER BY (CASE WHEN AVG(u.cpu_usage_pct) < 25 THEN 0 ELSE 1 END),
                 j.focus_billed_cost_sum DESC
        LIMIT %(n)s
    """, {"n": limit})


# Synthetic, illustrative monthly budget targets per provider (USD). Clearly
# labelled in the UI as illustrative — not a real ENBD budget.
_BUDGET_USD = {"AWS": 1200.0, "Microsoft": 780.0,
               "Oracle Cloud Infrastructure": 760.0, "__onprem__": 4000.0}


def cloud_vs_onprem_with_budget() -> dict:
    """Per-provider billed (USD) vs an illustrative budget + variance."""
    rows = db.query("""
        SELECT service_provider_name AS provider,
               SUM(billed_cost_usd)::NUMERIC(12,2) AS billed
        FROM focus_costs GROUP BY service_provider_name ORDER BY billed DESC""")
    out = []
    total_billed = 0.0
    total_target = 0.0
    for r in rows:
        billed = float(r["billed"] or 0)
        target = _BUDGET_USD.get(r["provider"], billed)
        var = round(100.0 * (billed - target) / target, 2) if target else 0.0
        total_billed += billed; total_target += target
        out.append({"provider": r["provider"], "source": "FOCUS · BilledCost (USD)",
                    "billed": billed, "target": target, "variance": var})
    onprem = float(db.query("SELECT COALESCE(SUM(billed_cost),0) AS t FROM miq_onprem_cost")[0]["t"] or 0)
    otgt = _BUDGET_USD["__onprem__"]
    total_billed += onprem; total_target += otgt
    out.append({"provider": "On-prem", "source": "Recharge · vCPU+GB model (USD)",
                "billed": onprem, "target": otgt,
                "variance": round(100.0*(onprem-otgt)/otgt, 2) if otgt else 0.0})
    return {"rows": out, "total_billed": round(total_billed, 2),
            "total_target": round(total_target, 2),
            "total_variance": round(100.0*(total_billed-total_target)/total_target, 2) if total_target else 0.0}


def workload_detail(vm_id: str) -> dict:
    """Everything the drill-down page needs for one workload."""
    jm = db.query("""
        SELECT miq_vm_id, miq_vm_name, miq_vendor, focus_source,
               focus_resource_id, focus_billed_cost_sum::NUMERIC(12,2) AS cost,
               miq_uid_ems, miq_ems_ref, join_key_used, status
        FROM resource_join_map WHERE miq_vm_id = %(id)s LIMIT 1""", {"id": str(vm_id)})
    if not jm:
        return {}
    head = jm[0]
    # miq_vm_id is TEXT: an 'ambiguous' join row stores comma-joined ids like
    # '12,13', so it isn't a single integer. miq_utilization keys on one numeric
    # VM id, so only fetch utilization when vm_id is a single integer — a
    # comma-joined/non-numeric id gets no util rows instead of a 500 (int()
    # ValueError). Default to a full None-valued dict (not {}) so the template's
    # `d.util.avg_cpu is not none` guards resolve rather than raising on a
    # missing attribute. (review finding)
    util = [{"avg_cpu": None, "max_cpu": None, "avg_mem": None,
             "max_mem": None, "samples": 0}]
    if str(vm_id).isdigit():
        util = db.query("""
            SELECT ROUND(AVG(cpu_usage_pct)::NUMERIC,1) AS avg_cpu,
                   ROUND(MAX(cpu_usage_pct)::NUMERIC,1) AS max_cpu,
                   ROUND(AVG(mem_usage_pct)::NUMERIC,1) AS avg_mem,
                   ROUND(MAX(mem_usage_pct)::NUMERIC,1) AS max_mem,
                   COUNT(*) AS samples
            FROM miq_utilization WHERE miq_vm_id = %(id)s""", {"id": int(vm_id)})
    # daily cost for this resource (from focus_costs joined by resource_id)
    daily = db.query("""
        SELECT charge_period_start::date AS day,
               SUM(billed_cost_usd)::NUMERIC(12,4) AS usd
        FROM focus_costs WHERE resource_id = %(rid)s
        GROUP BY 1 ORDER BY 1""", {"rid": head["focus_resource_id"]})
    # cost decomposition by service_name
    decomp = db.query("""
        SELECT service_name, SUM(billed_cost_usd)::NUMERIC(12,4) AS usd
        FROM focus_costs WHERE resource_id = %(rid)s
        GROUP BY service_name ORDER BY usd DESC""", {"rid": head["focus_resource_id"]})
    return {"head": head, "util": util[0], "daily": daily, "decomp": decomp}
