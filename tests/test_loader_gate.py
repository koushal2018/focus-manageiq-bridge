"""The loader's pre-commit conformance gate (W-16).

A destructive TRUNCATE+reload must NOT commit a non-conformant batch — doing so
would replace a good warehouse with a broken one. These tests prove the gate
raises inside the transaction so the caller rolls back. DB-backed; skip cleanly
without Postgres (same pattern as test_data_integrity)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _conn_or_skip():
    try:
        import psycopg2
        from db import loader
        conn = psycopg2.connect(**loader._conn_kwargs())
        return conn
    except Exception as e:
        pytest.skip(f"no Postgres reachable: {str(e).splitlines()[0]}")


def test_gate_passes_on_conformant_row():
    from db import loader
    conn = _conn_or_skip()
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            # Insert one fully-conformant row into a savepoint, check, roll back.
            cur.execute("""
                INSERT INTO focus_costs
                  (source, service_category, service_provider_name, billing_currency,
                   charge_period_start, charge_period_end, billed_cost, billed_cost_usd)
                VALUES ('test','Compute','AWS','USD',
                        '2026-06-01T00:00:00+00:00','2026-06-02T00:00:00+00:00', 1.0, 1.0)
            """)
            loader._assert_conformant_in_txn(cur)  # must NOT raise
    finally:
        conn.rollback()
        conn.close()


def test_gate_raises_on_null_charge_period_end():
    from db import loader
    conn = _conn_or_skip()
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO focus_costs
                  (source, service_category, service_provider_name, billing_currency,
                   charge_period_start, charge_period_end, billed_cost, billed_cost_usd)
                VALUES ('test','Compute','AWS','USD',
                        '2026-06-01T00:00:00+00:00', NULL, 1.0, 1.0)
            """)
            with pytest.raises(loader.LoadConformanceError) as ei:
                loader._assert_conformant_in_txn(cur)
            assert "charge_period_end" in str(ei.value)
    finally:
        conn.rollback()
        conn.close()


def test_gate_raises_on_out_of_set_service_category():
    from db import loader
    conn = _conn_or_skip()
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO focus_costs
                  (source, service_category, service_provider_name, billing_currency,
                   charge_period_start, charge_period_end, billed_cost, billed_cost_usd)
                VALUES ('test','Bananas','AWS','USD',
                        '2026-06-01T00:00:00+00:00','2026-06-02T00:00:00+00:00', 1.0, 1.0)
            """)
            with pytest.raises(loader.LoadConformanceError) as ei:
                loader._assert_conformant_in_txn(cur)
            assert "ServiceCategory" in str(ei.value)
    finally:
        conn.rollback()
        conn.close()
