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
        SELECT  j.miq_vm_name,
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
        GROUP BY j.miq_vm_name, j.miq_vendor, j.focus_source, j.focus_billed_cost_sum
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
