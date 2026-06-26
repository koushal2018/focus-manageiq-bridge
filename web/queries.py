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
    sql = """
        SELECT  service_provider_name,
                sku_meter,
                service_name,
                COUNT(*)         AS row_count,
                SUM(billed_cost) AS total_cost,
                billing_currency
        FROM    focus_costs
        WHERE   service_category = 'AI and Machine Learning'
        GROUP BY service_provider_name, sku_meter, service_name, billing_currency
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
# Requirement #3. Cloud half comes from focus_costs grouped by provider.
# On-prem half is the slice-6-pending estimate (currently a stub per the
# user's decision): we read miq_utilization to find on-prem VMs and apply
# the SPEC §0 calc (vCPU + memory + per-GB cost over 4y).
#
# On-prem candidate = a VM in miq_utilization whose join_map row has
# status='unmatched_miq_only'. The on-prem rate is a stub; slice 6
# replaces it with chargeback module reads.

ON_PREM_RATE_CPU_USD_PER_CORE_MONTH = 50.0
ON_PREM_RATE_MEM_USD_PER_GB_MONTH   = 5.0


def cloud_cost_by_provider() -> list[dict]:
    sql = """
        SELECT  service_provider_name,
                source,
                COUNT(*)                                 AS row_count,
                SUM(billed_cost)::NUMERIC(12,2)          AS total_cost,
                billing_currency
        FROM    focus_costs
        GROUP BY service_provider_name, source, billing_currency
        ORDER BY total_cost DESC
    """
    return db.query(sql)


def onprem_cost_estimate() -> list[dict]:
    """Stubbed on-prem cost from VMDB shape (cpu_cores + memory_mb).

    For each unmatched_miq_only row, we don't actually have cpu_cores in
    the join map (it's on the appliance side). For the stub view we
    expose what we DO have: the VM name and vendor, with a placeholder
    cost based on the workload definitions in generators/common.

    Slice 6 wires this to real chargeback rates and replaces this entire
    function.
    """
    # We rebuild from generators/common.WORKLOADS since the on-prem VMs
    # don't land in miq_utilization for the join (their resource_id is
    # > 90000 in the snapshot too --- they DO have rollups). Let's pull
    # cpu+mem from the union of miq_utilization (for rollup samples)
    # AND fall back to common.WORKLOADS for the sizing data.
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from generators import common as gen_common

    rows = []
    on_prem_workloads = [w for w in gen_common.WORKLOADS if w.is_on_prem_only()]
    for w in on_prem_workloads:
        gb = w.memory_mb / 1024.0
        # 4-year average monthly cost (ENBD's existing ManageIQ formula)
        monthly_usd = (
            ON_PREM_RATE_CPU_USD_PER_CORE_MONTH * w.cpu_cores +
            ON_PREM_RATE_MEM_USD_PER_GB_MONTH * gb
        )
        rows.append({
            "miq_vm_name": w.canonical_name,
            "business_unit": w.business_unit,
            "cpu_cores": w.cpu_cores,
            "memory_gb": round(gb, 1),
            "monthly_cost_usd": round(monthly_usd, 2),
            "avg_cpu_pct": w.cpu_pct,
            "avg_mem_pct": w.mem_pct,
        })
    return rows


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
