"""Canned parameterized NL queries.

Per SPEC §3.6: "Canned/parameterized queries first; free-text only after
guardrails work." Each entry here is a NAMED query with explicit parameter
slots --- no free-form SQL ever reaches the executor through this path.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Any, Callable

from web import db


@dataclasses.dataclass(frozen=True)
class CannedQuery:
    name: str
    description: str
    sql: str
    # name -> regex pattern. The regex MUST match the entire param string.
    params: dict[str, str] = dataclasses.field(default_factory=dict)


# Registry. Each query is read-only by construction --- no DML, no DDL.
QUERIES: dict[str, CannedQuery] = {
    "ai_cost_by_provider": CannedQuery(
        name="ai_cost_by_provider",
        description="Total AI/ML cost grouped by cloud provider.",
        sql="""
            SELECT service_provider_name,
                   SUM(billed_cost_usd)::NUMERIC(12,2) AS total_usd
            FROM   focus_costs
            WHERE  service_category = 'AI and Machine Learning'
            GROUP  BY service_provider_name
            ORDER  BY total_usd DESC
        """,
    ),
    "compute_cost_by_provider": CannedQuery(
        name="compute_cost_by_provider",
        description=(
            "LIKE-FOR-LIKE compute comparison: Compute *Usage* spend (USD) by "
            "provider. Excludes commitment Purchases, Tax, Credits and Refunds "
            "and non-compute services, so the providers are actually comparable "
            "— a raw all-category total is billing volume, not comparable cost."
        ),
        sql="""
            SELECT service_provider_name,
                   SUM(billed_cost_usd)::NUMERIC(12,2) AS compute_usage_usd,
                   COUNT(*)                            AS row_count
            FROM   focus_costs
            WHERE  service_category = 'Compute'
              AND  charge_category  = 'Usage'
            GROUP  BY service_provider_name
            ORDER  BY compute_usage_usd DESC
        """,
    ),
    "ai_cost_by_model": CannedQuery(
        name="ai_cost_by_model",
        description="AI/ML cost (USD) grouped by the model id captured in SkuMeter.",
        sql="""
            SELECT service_provider_name,
                   sku_meter,
                   SUM(billed_cost_usd)::NUMERIC(12,4) AS total_usd
            FROM   focus_costs
            WHERE  service_category = 'AI and Machine Learning'
            GROUP  BY service_provider_name, sku_meter
            ORDER  BY total_usd DESC
        """,
    ),
    "top_n_costly_workloads": CannedQuery(
        name="top_n_costly_workloads",
        description="Top N workloads by total billed cloud cost.",
        sql="""
            SELECT j.miq_vm_name,
                   j.miq_vendor,
                   j.focus_billed_cost_sum::NUMERIC(12,2) AS cost
            FROM   resource_join_map j
            WHERE  j.status = 'matched'
            ORDER  BY j.focus_billed_cost_sum DESC
            LIMIT  %(n)s
        """,
        params={"n": r"^\d{1,3}$"},  # 1-3 digit positive int
    ),
    "rightsizing_candidates": CannedQuery(
        name="rightsizing_candidates",
        description=(
            "Matched workloads where average CPU < cpu_max_pct over the "
            "rollup window but cost > min_cost. Defaults: cpu_max_pct=25, "
            "min_cost=10."
        ),
        sql="""
            SELECT j.miq_vm_name,
                   j.miq_vendor,
                   j.focus_billed_cost_sum::NUMERIC(12,2) AS cost,
                   ROUND(AVG(u.cpu_usage_pct)::NUMERIC, 2) AS avg_cpu_pct,
                   ROUND(AVG(u.mem_usage_pct)::NUMERIC, 2) AS avg_mem_pct
            FROM   resource_join_map j
            JOIN   miq_utilization u ON u.miq_vm_id = j.miq_vm_id::BIGINT
            WHERE  j.status = 'matched'
            GROUP  BY j.miq_vm_name, j.miq_vendor, j.focus_billed_cost_sum
            HAVING AVG(u.cpu_usage_pct) < %(cpu_max_pct)s
              AND j.focus_billed_cost_sum > %(min_cost)s
            ORDER  BY j.focus_billed_cost_sum DESC
        """,
        params={"cpu_max_pct": r"^\d{1,3}(\.\d+)?$", "min_cost": r"^\d+(\.\d+)?$"},
    ),
    "on_prem_total_by_business_unit": CannedQuery(
        name="on_prem_total_by_business_unit",
        description="On-prem recharge total grouped by business unit (sub_account_id).",
        sql="""
            SELECT sub_account_id AS business_unit,
                   SUM(billed_cost)::NUMERIC(12,2) AS monthly_total,
                   billing_currency
            FROM   miq_onprem_cost
            GROUP  BY sub_account_id, billing_currency
            ORDER  BY monthly_total DESC
        """,
    ),
}


class CannedError(ValueError):
    """Raised when a canned query call is invalid."""


def run_canned(name: str, params: dict[str, Any] | None = None) -> dict:
    """Execute a registered canned query. Returns {sql, params, rows}."""
    params = params or {}
    if name not in QUERIES:
        raise CannedError(
            f"unknown canned query {name!r}; "
            f"available: {sorted(QUERIES.keys())}"
        )
    q = QUERIES[name]

    # Required-params check: every regex'd slot must be present.
    for slot, pattern in q.params.items():
        if slot not in params:
            raise CannedError(f"missing param {slot!r} for {name!r}")
        val = str(params[slot])
        if not re.fullmatch(pattern, val):
            raise CannedError(
                f"param {slot!r}={val!r} does not match {pattern!r}"
            )
    # Reject any params the query doesn't declare --- prevents accidental
    # injection of unexpected slots.
    extra = set(params) - set(q.params)
    if extra:
        raise CannedError(f"unexpected params: {sorted(extra)}")

    # psycopg2 named-style params --- safe (the driver escapes).
    rows = db.query(q.sql, params if q.params else None)
    return {
        "name": q.name,
        "description": q.description,
        "sql": q.sql.strip(),
        "params": params,
        "rows": rows,
    }
