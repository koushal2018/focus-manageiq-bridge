"""SQL validator for free-text NL-query answers.

Per SPEC §3.6 and GOTCHA-to-be: every SQL the model produces is parsed
with sqlglot, walked, and rejected if it:
  - is not exactly one statement,
  - is not a SELECT (CTEs allowed if final node is SELECT),
  - references any table not in the readonly allowlist,
  - contains any DDL/DML/transaction-control nodes (INSERT, UPDATE,
    DELETE, MERGE, TRUNCATE, ALTER, CREATE, DROP, GRANT, REVOKE,
    COPY, EXECUTE, LOCK, BEGIN, COMMIT, ROLLBACK).

The goal is NOT to block clever SQL --- it's to fail closed if the
model returns anything other than a read of the four FinOps tables.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp


READONLY_TABLES = {
    "focus_costs",
    "resource_join_map",
    "miq_utilization",
    "miq_onprem_cost",
}


# sqlglot node types that ARE permitted (the read-only path).
ALLOWED_TOP_LEVEL = (exp.Select, exp.Union, exp.With)


@dataclass
class SqlValidationError(Exception):
    reason: str
    sql: str

    def __str__(self) -> str:  # type: ignore[override]
        return f"SQL rejected: {self.reason}"


def validate(sql: str) -> sqlglot.exp.Expression:
    """Parse + validate. Returns the parsed AST on success; raises on reject."""
    sql_stripped = sql.strip().rstrip(";").strip()
    if not sql_stripped:
        raise SqlValidationError("empty SQL", sql)

    # Reject multi-statement up front (cheap check before parse).
    # sqlglot.parse() returns a list; we want exactly one.
    parsed = sqlglot.parse(sql_stripped, dialect="postgres")
    if len(parsed) != 1:
        raise SqlValidationError(
            f"expected 1 statement, got {len(parsed)}", sql
        )
    tree = parsed[0]
    if tree is None:
        raise SqlValidationError("could not parse SQL", sql)

    # Must be SELECT (possibly wrapped in WITH).
    if not isinstance(tree, ALLOWED_TOP_LEVEL):
        raise SqlValidationError(
            f"top-level must be SELECT/UNION/WITH, got {type(tree).__name__}",
            sql,
        )

    # If WITH, the inner statement must also be a SELECT/UNION.
    if isinstance(tree, exp.With):
        inner = tree.this
        if not isinstance(inner, (exp.Select, exp.Union)):
            raise SqlValidationError(
                f"WITH must wrap a SELECT/UNION, got {type(inner).__name__}",
                sql,
            )

    # Walk for any forbidden node. We deny by node class --- safer than
    # allowlisting an open-ended set of node types.
    forbidden = (
        exp.Insert, exp.Update, exp.Delete, exp.Merge,
        exp.Create, exp.Drop, exp.Alter, exp.AlterColumn,
        exp.TruncateTable,
        exp.Grant,
        exp.Transaction, exp.Commit, exp.Rollback,
        exp.Use, exp.Copy,
        exp.Command,            # generic catch-all for unparsed verbs
    )
    for node in tree.walk():
        if isinstance(node, forbidden):
            raise SqlValidationError(
                f"forbidden node: {type(node).__name__}", sql
            )

    # Collect CTE aliases so we don't treat them as physical tables.
    cte_aliases: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        alias = cte.alias_or_name
        if alias:
            cte_aliases.add(alias.lower())

    # Every table reference must be in the allowlist or be a CTE alias.
    for tbl in tree.find_all(exp.Table):
        name = (tbl.name or "").lower()
        if name in cte_aliases:
            continue  # references a CTE we already declared
        if name not in READONLY_TABLES:
            raise SqlValidationError(
                f"table {name!r} not in readonly allowlist", sql
            )
        # Reject any non-default schema qualifier
        if tbl.db and tbl.db.lower() not in ("", "public"):
            raise SqlValidationError(
                f"schema {tbl.db!r} not allowed (use unqualified or 'public')",
                sql,
            )

    return tree


def financial_sanity_warnings(sql: str) -> list[str]:
    """Flag financially-dangerous-but-valid SQL (GOTCHA H-10).

    The allowlist guard stops injection; it does NOT stop a query that runs
    fine and returns a WRONG number. The single most likely such error here
    is SUM-ing the raw `billed_cost`, which mixes AED and USD across
    providers (H-1). We warn (and the caller surfaces it) rather than
    silently trusting a cross-currency total.
    """
    warnings: list[str] = []
    low = " ".join(sql.lower().split())
    # SUM/AVG/total over the raw currency-mixed column without the _usd suffix.
    import re
    if re.search(r"(sum|avg)\s*\(\s*billed_cost\s*\)", low):
        warnings.append(
            "aggregates raw billed_cost — mixes AED and USD across providers. "
            "Use billed_cost_usd for cross-provider totals (H-1)."
        )
    # Aggregating cost without grouping or filtering by currency is suspicious
    # if it touches focus_costs and the raw column.
    if "focus_costs" in low and "billed_cost" in low and "billed_cost_usd" not in low \
            and re.search(r"(sum|avg)\s*\(", low):
        warnings.append(
            "cost aggregate on focus_costs does not use the USD-normalized "
            "column; verify currencies are not being mixed."
        )
    return warnings


def is_readonly(sql: str) -> tuple[bool, str | None]:
    """Wrapper that returns (ok, reason)."""
    try:
        validate(sql)
        return True, None
    except SqlValidationError as e:
        return False, e.reason
