"""Pure-logic tests for the NL-query SQL guard (no DB needed).

These protect the bank-safety invariant (SPEC §0): the guard must reject
anything that isn't a single read-only SELECT against the allowlisted tables,
and the financial-sanity warnings must flag the B-6/B-7 bug classes.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai import sql_guard


# --- the guard must ACCEPT legitimate read-only queries ---------------------
@pytest.mark.parametrize("sql", [
    "SELECT * FROM focus_costs LIMIT 10",
    "SELECT service_provider_name, SUM(billed_cost_usd) FROM focus_costs GROUP BY 1",
    "WITH x AS (SELECT miq_vm_id, AVG(cpu_usage_pct) c FROM miq_utilization GROUP BY 1)"
    " SELECT * FROM x",
    "SELECT * FROM resource_join_map WHERE status = 'matched'",
])
def test_guard_allows_readonly_select(sql):
    sql_guard.validate(sql)  # must not raise


# --- the guard must REJECT non-SELECT / non-allowlisted / multi-statement ----
@pytest.mark.parametrize("sql", [
    "DROP TABLE focus_costs",
    "DELETE FROM miq_utilization",
    "UPDATE focus_costs SET billed_cost = 0",
    "INSERT INTO focus_costs (row_id) VALUES (1)",
    "SELECT * FROM pg_catalog.pg_tables",          # not allowlisted
    "SELECT * FROM information_schema.columns",     # not allowlisted
    "SELECT 1; DROP TABLE focus_costs",             # multi-statement
    "TRUNCATE focus_costs",
    "GRANT ALL ON focus_costs TO public",
])
def test_guard_rejects_unsafe(sql):
    with pytest.raises(sql_guard.SqlValidationError):
        sql_guard.validate(sql)


def test_is_readonly_wrapper():
    ok, reason = sql_guard.is_readonly("SELECT 1 FROM focus_costs")
    assert ok and reason is None
    ok, reason = sql_guard.is_readonly("DROP TABLE focus_costs")
    assert not ok and reason


# --- financial sanity: the B-6 / B-7 bug classes must be flagged ------------
def test_warns_on_raw_billed_cost_sum():
    # B-7: summing raw billed_cost (mixes AED + USD) must warn.
    w = sql_guard.financial_sanity_warnings(
        "SELECT SUM(billed_cost) FROM focus_costs")
    assert any("billed_cost" in x for x in w)


def test_warns_on_join_fanout():
    # B-6: summing focus cost while joined to miq_utilization fans out.
    w = sql_guard.financial_sanity_warnings(
        "SELECT SUM(fc.billed_cost_usd) FROM focus_costs fc "
        "JOIN resource_join_map j ON j.focus_resource_id=fc.resource_id "
        "JOIN miq_utilization u ON u.miq_vm_id=j.miq_vm_id::bigint")
    assert any("fan" in x.lower() or "util" in x.lower() for x in w)


def test_no_warning_on_correct_usd_aggregate():
    # The correct pattern (USD column, no util join) must be clean.
    w = sql_guard.financial_sanity_warnings(
        "SELECT service_provider_name, SUM(billed_cost_usd) "
        "FROM focus_costs GROUP BY 1")
    assert w == []
