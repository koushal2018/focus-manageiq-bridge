"""DB-backed integrity tests — the bugs that shipped twice (B-6, B-7).

These need a seeded Postgres. They SKIP cleanly when one isn't reachable
(e.g. CI without the compose stack), so the pure-logic suite still gates
every push. Run locally inside the web container, or set FOCUS_PG_* to a
seeded DB.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _db_or_skip():
    """Return the db module, or skip the whole test if no DB is reachable."""
    try:
        from web import db
        db.query("SELECT 1")
        return db
    except Exception as e:  # psycopg2 OperationalError, import error, etc.
        pytest.skip(f"no seeded Postgres reachable: {str(e).splitlines()[0]}")


def test_join_cost_reconciles_to_focus_costs():
    """B-7: every matched workload's pre-summed cost must equal the live
    USD sum from focus_costs for the same resource_id. (Azure rows were
    3.6725x inflated when the join summed raw AED billed_cost.)"""
    db = _db_or_skip()
    rows = db.query("""
        SELECT j.miq_vm_name,
               j.focus_billed_cost_sum::numeric(12,2) AS jm,
               (SELECT SUM(billed_cost_usd) FROM focus_costs f
                WHERE f.resource_id = j.focus_resource_id)::numeric(12,2) AS live
        FROM resource_join_map j
        WHERE j.status = 'matched'""")
    mismatches = [(r["miq_vm_name"], r["jm"], r["live"]) for r in rows if r["jm"] != r["live"]]
    assert mismatches == [], f"join cost != focus_costs for: {mismatches}"


def test_no_null_billed_cost_usd_on_billable_rows():
    """H-1: every row with a billed_cost must carry the USD-normalized value."""
    db = _db_or_skip()
    n = db.query("""
        SELECT COUNT(*) AS n FROM focus_costs
        WHERE billed_cost IS NOT NULL AND billed_cost_usd IS NULL""")[0]["n"]
    assert n == 0, f"{n} billable rows missing billed_cost_usd"


def test_service_category_in_allowed_set():
    """F-2: ServiceCategory is a closed FOCUS set; no free-text values."""
    db = _db_or_skip()
    allowed = {
        "AI and Machine Learning", "Analytics", "Business Applications", "Compute",
        "Databases", "Developer Tools", "Multicloud", "Identity", "Integration",
        "Internet of Things", "Management and Governance", "Media", "Migration",
        "Mobile", "Networking", "Security", "Storage", "Web", "Other",
    }
    cats = {r["c"] for r in db.query(
        "SELECT DISTINCT service_category AS c FROM focus_costs WHERE service_category IS NOT NULL")}
    bad = cats - allowed
    assert not bad, f"non-conformant ServiceCategory values: {bad}"


def test_conformance_report_passes():
    """The dashboard's own conformance check must be green on seeded data."""
    db = _db_or_skip()
    from web import queries
    c = queries.focus_conformance()
    assert c["conformant"], f"{c['total_fail_rows']} rows failed conformance"
