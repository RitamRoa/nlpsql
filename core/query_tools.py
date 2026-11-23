"""Deterministic explanation, cost, narration, and SQL dialect helpers."""
from dataclasses import dataclass
from numbers import Number

import sqlglot
from sqlglot import exp

from .adapters.base import Result
from .validator import validate


@dataclass
class QueryExplanation:
    summary: str
    tables: list[str]
    columns: list[str]
    trace: list[str]


def explain_query(sql: str, dialect: str = "sqlite") -> QueryExplanation:
    tree = sqlglot.parse_one(sql, read=dialect)
    if not isinstance(tree, exp.Query):
        raise ValueError("Only SELECT queries can be explained.")
    tables = sorted({table.name for table in tree.find_all(exp.Table)})
    columns = sorted({
        f"{column.table}.{column.name}" if column.table else column.name
        for column in tree.find_all(exp.Column)
        if column.name != "*"
    })
    features = []
    if tree.find(exp.Join):
        features.append("joins related tables")
    if tree.find(exp.AggFunc):
        features.append("aggregates values")
    if tree.find(exp.Where):
        features.append("filters rows")
    if tree.args.get("group"):
        features.append("groups the results")
    if tree.args.get("order"):
        features.append("sorts the results")
    summary = "This query " + (", ".join(features) or "retrieves matching records") + "."
    trace = [f"Read table {name}." for name in tables]
    trace.extend(f"Used column {name}." for name in columns)
    return QueryExplanation(summary, tables, columns, trace)


def verified_narration(result: Result) -> str:
    """Summarize only counts and numeric aggregates computed from returned rows."""
    row_count = len(result.rows)
    truncated_note = " The displayed rows are truncated." if result.truncated else ""
    parts = [f"The query returned {row_count} row{'s' if row_count != 1 else ''}."]
    for index, name in enumerate(result.columns):
        numeric_values = [
            row[index] for row in result.rows
            if index < len(row) and isinstance(row[index], Number)
            and not isinstance(row[index], bool)
        ]
        if not numeric_values:
            continue
        total = sum(numeric_values)
        average = total / len(numeric_values)
        parts.append(
            f"For {name}, the displayed rows sum to {total:g} "
            f"(average {average:g})."
        )
    return " ".join(parts) + truncated_note


def transpile_sql(sql: str, source_dialect: str, target_dialect: str) -> str:
    """Transpile one validated read-only query to another SQL dialect."""
    validate(sql, _schema_for_validation(sql, source_dialect), source_dialect)
    statements = sqlglot.transpile(sql, read=source_dialect, write=target_dialect)
    if len(statements) != 1:
        raise ValueError("Transpilation must produce exactly one SQL statement.")
    return statements[0]


def _schema_for_validation(sql: str, dialect: str):
    """Build the referenced shape for AST safety checks in the standalone tool."""
    from .adapters.base import Column, Schema, Table

    tree = sqlglot.parse_one(sql, read=dialect)
    tables: dict[str, set[str]] = {}
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if table.name.lower() not in ctes:
            tables.setdefault(table.name, set())
    all_columns = {
        column.name for column in tree.find_all(exp.Column) if column.name != "*"
    }
    for table_name in tables:
        tables[table_name].update(all_columns)
    return Schema([
        Table(name, [Column(column, "TEXT") for column in columns])
        for name, columns in tables.items()
    ])


def cost_guard(adapter, sql: str) -> dict:
    """Run a read-only EXPLAIN and flag obvious scans and wide joins."""
    dialect = adapter.dialect().lower()
    prefix = "EXPLAIN QUERY PLAN " if dialect == "sqlite" else "EXPLAIN "
    result = adapter.execute_readonly(prefix + sql, timeout_s=5.0, limit=200)
    plan_lines = [" ".join(str(value) for value in row if value is not None) for row in result.rows]
    plan_text = "\n".join(plan_lines)
    tree = sqlglot.parse_one(sql, read=dialect)
    warnings = []
    scans = [
        line for line in plan_lines
        if "SCAN " in line.upper() and "USING INDEX" not in line.upper()
    ]
    if scans:
        warnings.append("The query plan includes a table scan; consider indexing columns used in filters or joins.")
    joins = len(list(tree.find_all(exp.Join)))
    if joins >= 4:
        warnings.append(f"The query joins {joins} tables; review the join plan for cost.")
    return {
        "plan": plan_text,
        "warnings": warnings,
        "join_count": joins,
        "full_scan_count": len(scans),
    }
