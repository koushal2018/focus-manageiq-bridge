"""Thin Postgres connection helper for the web layer.

Defaults point at the standalone finops_pg container started after LM-1.
Override with FOCUS_PG_HOST / FOCUS_PG_PORT / FOCUS_PG_USER / FOCUS_PG_PASS
when running against a different DB.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
import psycopg2.extras
from psycopg2 import sql as pgsql


def _conn_kwargs() -> dict[str, Any]:
    return {
        "host":     os.environ.get("FOCUS_PG_HOST", "127.0.0.1"),
        "port": int(os.environ.get("FOCUS_PG_PORT", "5432")),
        "user":     os.environ.get("FOCUS_PG_USER", "focus_app"),
        "password": os.environ.get("FOCUS_PG_PASS", "focus_app_demo"),
        "dbname":   os.environ.get("FOCUS_PG_DB",   "focus"),
        "connect_timeout": 5,
    }


@contextmanager
def get_conn() -> Iterator[psycopg2.extensions.connection]:
    """Open a connection per request. PoC scale; no pool yet."""
    c = psycopg2.connect(**_conn_kwargs())
    try:
        yield c
    finally:
        c.close()


def query(sql: str | pgsql.Composed, params: tuple | dict | None = None) -> list[dict]:
    """Run a SELECT and return rows as list[dict].

    Accepts psycopg2.sql.Composed so callers that need a dynamic identifier
    (a table/column name, which CANNOT be a bind parameter) compose it with
    pgsql.Identifier(...) instead of f-string interpolation.
    """
    with get_conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def query_untrusted(sql: str, timeout_ms: int = 5000) -> list[dict]:
    """Run SQL that did NOT originate in this codebase (the AI NL-query path).

    The sql_guard AST allowlist is the first gate, but any validator over
    model-generated SQL is best-effort — so this path adds DATABASE-level
    enforcement the guard cannot be talked out of:
      - READ ONLY transaction: Postgres itself rejects any write/DDL that
        slipped past the guard (SET TRANSACTION READ ONLY precedes the query
        inside the same implicit transaction).
      - statement_timeout: caps a pathological query so it can't be a DoS.
    Production posture (EBA): run this through a dedicated DB role with
    SELECT-only grants on the four FinOps tables — see GOTCHAS SEC-5.
    """
    with get_conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout = %s", (timeout_ms,))
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]
