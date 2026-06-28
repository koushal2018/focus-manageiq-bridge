"""Canned NL-query tests. The SQL-shape checks are pure logic (guard allowlist);
the execution checks are DB-backed and skip without Postgres."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai import canned, sql_guard


def test_compute_cost_by_provider_is_registered_and_like_for_like():
    q = canned.QUERIES["compute_cost_by_provider"]
    # The whole point: it must filter to Compute Usage, not sum everything.
    assert "service_category = 'Compute'" in q.sql
    assert "charge_category  = 'Usage'" in q.sql or "charge_category = 'Usage'" in q.sql
    # USD-normalized (never raw billed_cost — B-6/B-7).
    assert "billed_cost_usd" in q.sql
    assert "SUM(billed_cost)" not in q.sql.replace("billed_cost_usd", "")


def test_compute_cost_query_passes_sql_guard():
    # Must be an allowlisted read-only SELECT (no DML, only focus_costs).
    sql_guard.validate(canned.QUERIES["compute_cost_by_provider"].sql)


def test_unit_price_query_registered_and_guarded():
    q = canned.QUERIES["compute_unit_price_by_provider"]
    # It must compare UNIT PRICE (not a spend total) and normalize AED→USD.
    assert "list_unit_price" in q.sql
    assert "fx_rate_to_usd" in q.sql            # AED normalization
    assert "service_category = 'Compute'" in q.sql
    sql_guard.validate(q.sql)


def test_unit_price_query_shows_provider_spread():
    db = _db_or_skip()
    res = canned.run_canned("compute_unit_price_by_provider")
    prices = {r["service_provider_name"]: float(r["list_usd_per_vcpu_hour"])
              for r in res["rows"]}
    assert len(prices) >= 3
    # the providers genuinely differ — a comparison is meaningful (FIN-2)
    assert max(prices.values()) - min(prices.values()) > 0.005, prices


def _db_or_skip():
    try:
        from web import db
        db.query("SELECT 1")
        return db
    except Exception as e:
        pytest.skip(f"no seeded Postgres: {str(e).splitlines()[0]}")


def test_compute_cost_excludes_non_usage_charges():
    """Like-for-like total must be strictly LESS than the all-category total
    for at least one provider that carries a commitment Purchase — proving the
    query excludes the one-off charges that distort a raw total (the bug this
    fixes: OCI's total is inflated by a $7,200 commitment Purchase)."""
    db = _db_or_skip()
    res = canned.run_canned("compute_cost_by_provider")
    like = {r["service_provider_name"]: float(r["compute_usage_usd"]) for r in res["rows"]}
    total = {r["service_provider_name"]: float(r["usd"]) for r in db.query(
        "SELECT service_provider_name, SUM(billed_cost_usd) usd FROM focus_costs GROUP BY 1")}
    # every provider's like-for-like compute <= its all-category total
    for p, v in like.items():
        assert v <= total[p] + 0.01, f"{p}: compute {v} > total {total[p]}"
    # and for at least one provider it is strictly, materially smaller
    assert any(like[p] < total[p] - 1.0 for p in like), \
        "expected the like-for-like figure to exclude non-usage charges"
