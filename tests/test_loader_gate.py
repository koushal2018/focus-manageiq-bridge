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


def test_load_source_replaces_only_its_partition(tmp_path):
    """W-15: load_source(sid, csv) must DELETE+INSERT only sid's rows, leaving
    every other source's rows untouched — and a second load of the same sid
    REPLACES (not appends) its partition. The dedicated, committed-state version
    of the live e2e proof, guarded in the suite."""
    from db import loader
    from web import db
    conn = _conn_or_skip()
    conn.close()  # only used to skip cleanly without a DB

    sid = "test-inc-partition"
    hdr = ("_source,_source_id,ServiceCategory,BillingCurrency,BilledCost,"
           "ChargePeriodStart,ChargePeriodEnd,ServiceProviderName,ResourceId")

    def _csv(n):
        p = tmp_path / f"{sid}-{n}.csv"
        lines = [hdr] + [
            f"upload,{sid},Compute,USD,{1.0+i},2026-06-01T00:00:00+00:00,"
            f"2026-06-02T00:00:00+00:00,AWS,i-part-{i}"
            for i in range(n)
        ]
        p.write_text("\n".join(lines) + "\n")
        return str(p)

    base_others = db.query(
        "SELECT COUNT(*) n FROM focus_costs WHERE source_id IS DISTINCT FROM %(s)s",
        {"s": sid})[0]["n"]
    try:
        loader.load_source(sid, _csv(5))
        assert db.query("SELECT COUNT(*) n FROM focus_costs WHERE source_id=%(s)s",
                        {"s": sid})[0]["n"] == 5
        others = db.query(
            "SELECT COUNT(*) n FROM focus_costs WHERE source_id IS DISTINCT FROM %(s)s",
            {"s": sid})[0]["n"]
        assert others == base_others, "other sources were mutated by a per-source load"

        # Re-load same source with fewer rows → partition REPLACED, not appended.
        loader.load_source(sid, _csv(3))
        assert db.query("SELECT COUNT(*) n FROM focus_costs WHERE source_id=%(s)s",
                        {"s": sid})[0]["n"] == 3
        assert db.query(
            "SELECT COUNT(*) n FROM focus_costs WHERE source_id IS DISTINCT FROM %(s)s",
            {"s": sid})[0]["n"] == base_others
    finally:
        # clean our partition so we don't pollute the shared seeded DB
        import psycopg2
        c = psycopg2.connect(**loader._conn_kwargs())
        c.autocommit = True
        with c.cursor() as cur:
            cur.execute("DELETE FROM focus_costs WHERE source_id=%s", (sid,))
        c.close()
