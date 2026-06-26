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


def query(sql: str, params: tuple | dict | None = None) -> list[dict]:
    """Run a SELECT and return rows as list[dict]."""
    with get_conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
