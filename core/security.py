"""Local role-based access controls and privacy-conscious query auditing."""
import hashlib
import hmac
import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml
import sqlglot
from sqlglot import exp

from .adapters.base import Result, Schema, Table
from .validator import ValidationError, validate


@dataclass
class RolePolicy:
    schemas: list[str] = field(default_factory=lambda: ["*"])
    tables: dict[str, dict] = field(default_factory=dict)
    allow_writes: bool = False


@dataclass
class AccessPolicy:
    roles: dict[str, RolePolicy] = field(default_factory=dict)
    enabled: bool = False

    @classmethod
    def from_environment(cls) -> "AccessPolicy":
        path = os.environ.get("NLPSQL_ACCESS_POLICY")
        if not path:
            return cls()
        try:
            with Path(path).open("r", encoding="utf-8") as file:
                data = yaml.safe_load(file)
        except (OSError, yaml.YAMLError) as error:
            raise ValueError(f"Could not load access policy '{path}': {error}") from error
        if not isinstance(data, dict) or set(data) - {"roles"}:
            raise ValueError("Access policy must contain only a roles mapping.")
        raw_roles = data.get("roles")
        if not isinstance(raw_roles, dict) or not raw_roles:
            raise ValueError("Access policy must define at least one role.")
        roles = {}
        for role_name, raw_role in raw_roles.items():
            if not isinstance(role_name, str) or not role_name or not isinstance(raw_role, dict):
                raise ValueError("Each access-policy role must map a name to a mapping.")
            if set(raw_role) - {"schemas", "tables", "allow_writes"}:
                raise ValueError(f"Unknown settings for role '{role_name}'.")
            schemas = raw_role.get("schemas", ["*"])
            tables = raw_role.get("tables", {})
            allow_writes = raw_role.get("allow_writes", False)
            if not isinstance(schemas, list) or not all(isinstance(item, str) for item in schemas):
                raise ValueError(f"Role '{role_name}' schemas must be a list of strings.")
            if not isinstance(tables, dict) or not isinstance(allow_writes, bool):
                raise ValueError(f"Role '{role_name}' has invalid tables or allow_writes.")
            for table, options in tables.items():
                if not isinstance(table, str) or not isinstance(options, dict):
                    raise ValueError(f"Role '{role_name}' table policies must be mappings.")
                if set(options) - {"columns", "row_filter", "masked_columns"}:
                    raise ValueError(f"Unknown table policy settings for '{table}'.")
                for key in ("columns", "masked_columns"):
                    values = options.get(key, [])
                    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                        raise ValueError(f"{role_name}.{table}.{key} must be a list of strings.")
                row_filter = options.get("row_filter")
                if row_filter is not None and not isinstance(row_filter, str):
                    raise ValueError(f"{role_name}.{table}.row_filter must be a string.")
            roles[role_name] = RolePolicy(schemas, tables, allow_writes)
        return cls(roles=roles, enabled=True)

    def role_for_token(self, token: str | None) -> str | None:
        if not self.enabled:
            return "local_operator"
        if not token:
            return None
        try:
            token_roles = json.loads(os.environ.get("NLPSQL_ROLE_TOKENS", "{}"))
        except json.JSONDecodeError as error:
            raise ValueError("NLPSQL_ROLE_TOKENS must be a JSON object.") from error
        if not isinstance(token_roles, dict):
            raise ValueError("NLPSQL_ROLE_TOKENS must be a JSON object.")
        for candidate, role in token_roles.items():
            if isinstance(candidate, str) and hmac.compare_digest(candidate, token):
                return role if role in self.roles else None
        return None

    def role(self, role_name: str | None) -> RolePolicy:
        if not self.enabled:
            return RolePolicy()
        if role_name not in self.roles:
            raise PermissionError("A configured access role is required.")
        return self.roles[role_name]

    def authorize_schema(self, role_name: str | None, schema_name: str) -> RolePolicy:
        role = self.role(role_name)
        if "*" not in role.schemas and schema_name.lower() not in {
            name.lower() for name in role.schemas
        }:
            raise PermissionError(f"Role '{role_name}' cannot access schema '{schema_name}'.")
        return role

    def restrict_schema(self, schema: Schema, role: RolePolicy) -> Schema:
        """Return the portion of a schema visible to this role."""
        if not self.enabled:
            return schema
        allowed = {name.lower(): options for name, options in role.tables.items()}
        tables = []
        for table in schema.tables:
            options = allowed.get(table.name.lower())
            if options is None:
                continue
            permitted_columns = {name.lower() for name in options.get("columns", [])}
            columns = [
                column for column in table.columns
                if not permitted_columns or column.name.lower() in permitted_columns
            ]
            tables.append(Table(table.name, columns, table.foreign_keys))
        return Schema(tables)

    def enforce_query(
        self,
        sql: str,
        schema: Schema,
        role: RolePolicy,
        dialect: str,
        full_schema: Schema | None = None,
    ) -> str:
        """Validate a SELECT against the role-visible schema and apply row filters."""
        validate(sql, schema, dialect)
        if not self.enabled:
            return sql
        statements = [statement for statement in sqlglot.parse(sql, read=dialect) if statement]
        tree = statements[0]
        if not isinstance(tree, exp.Query):
            raise ValidationError("Only read-only queries are allowed.")

        table_policies = {name.lower(): options for name, options in role.tables.items()}
        cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        table_nodes = list(tree.find_all(exp.Table))
        for table_node in table_nodes:
            name = table_node.name
            if name.lower() in cte_names:
                continue
            options = table_policies.get(name.lower())
            if options is None:
                raise PermissionError(f"Role cannot access table '{name}'.")
        table_aliases = {
            (table.alias_or_name or table.name).lower(): table.name.lower()
            for table in table_nodes
        }
        masked_by_table = {
            name.lower(): {column.lower() for column in options.get("masked_columns", [])}
            for name, options in table_policies.items()
        }
        for select in tree.find_all(exp.Select):
            for projection in select.expressions:
                is_star = isinstance(projection, exp.Star) or (
                    isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)
                )
                if is_star:
                    involved = {
                        table.name.lower() for table in select.find_all(exp.Table)
                        if table.name.lower() not in cte_names
                    }
                    if any(
                        table_policies.get(table, {}).get("columns")
                        or masked_by_table.get(table)
                        for table in involved
                    ):
                        raise ValidationError(
                            "SELECT * is disabled when role policies restrict or mask columns."
                        )

        projection_ids = set()
        for select in tree.find_all(exp.Select):
            for projection in select.expressions:
                column = projection.this if isinstance(projection, exp.Alias) else projection
                if not isinstance(column, exp.Column):
                    continue
                table_name = table_aliases.get(column.table.lower(), column.table.lower()) if column.table else None
                applicable_tables = [table_name] if table_name else list(table_policies)
                if any(column.name.lower() in masked_by_table.get(name, set()) for name in applicable_tables):
                    projection_ids.add(id(column))

        for column in tree.find_all(exp.Column):
            table_name = table_aliases.get(column.table.lower(), column.table.lower()) if column.table else None
            applicable_tables = [table_name] if table_name else list(table_policies)
            is_masked = any(
                column.name.lower() in masked_by_table.get(name, set())
                for name in applicable_tables
            )
            if is_masked and id(column) not in projection_ids:
                raise PermissionError(
                    f"Role may not filter, sort, or calculate using masked column '{column.name}'."
                )

        # Add each table's row predicate to each SELECT that reads that table.
        for select in tree.find_all(exp.Select):
            local_tables = [
                table for table in select.find_all(exp.Table)
                if table.name.lower() not in cte_names
                and table.find_ancestor(exp.Select) is select
            ]
            predicates = []
            for table_node in local_tables:
                table_name = table_node.name.lower()
                options = table_policies.get(table_name)
                if options and options.get("row_filter"):
                    condition = options["row_filter"]
                    try:
                        parsed = sqlglot.parse_one(f"SELECT 1 WHERE {condition}", read=dialect)
                    except Exception as error:
                        raise ValueError(
                            f"Invalid row filter for table '{table_name}': {error}"
                        ) from error
                    where = parsed.args.get("where")
                    if where is None:
                        raise ValueError(f"Invalid row filter for table '{table_name}'.")
                    validate(
                        f"SELECT * FROM {table_name} WHERE {condition}",
                        full_schema or schema,
                        dialect,
                    )
                    predicate = where.this.copy()
                    alias = table_node.alias_or_name or table_node.name
                    for column in predicate.find_all(exp.Column):
                        if not column.table:
                            column.set("table", exp.to_identifier(alias))
                    predicates.append(predicate)
            if predicates:
                combined = predicates[0]
                for predicate in predicates[1:]:
                    combined = exp.and_(combined, predicate)
                select.where(combined, append=True, copy=False)
        rewritten_sql = tree.sql(dialect=dialect)
        return rewritten_sql

    def enforce_write(
        self,
        sql: str,
        schema: Schema,
        role: RolePolicy,
        dialect: str,
    ) -> str:
        if not role.allow_writes:
            raise PermissionError("This role is not permitted to write.")
        statements = [statement for statement in sqlglot.parse(sql, read=dialect) if statement]
        if len(statements) != 1 or not isinstance(statements[0], (exp.Insert, exp.Update)):
            raise ValidationError("Only one INSERT or UPDATE statement can be previewed.")
        tree = statements[0]
        if tree.args.get("returning"):
            raise ValidationError("RETURNING is not allowed in write previews.")
        if any(isinstance(node, (exp.Subquery, exp.Func, exp.Command)) for node in tree.walk()):
            raise ValidationError("Subqueries and functions are not allowed in write previews.")
        target = tree.this.this if isinstance(tree, exp.Insert) and isinstance(tree.this, exp.Schema) else tree.this
        if not isinstance(target, exp.Table):
            raise ValidationError("Write target must be one table.")
        table_name = target.name
        table = next((item for item in schema.tables if item.name.lower() == table_name.lower()), None)
        if table is None:
            raise ValidationError(f"Unknown write target table: {table_name}")
        table_options = next(
            (options for name, options in role.tables.items() if name.lower() == table_name.lower()),
            None,
        )
        if table_options is None:
            raise PermissionError(f"Role cannot access table '{table_name}'.")
        if table_options.get("row_filter"):
            raise PermissionError("Writes to row-filtered tables are disabled.")

        schema_columns = {column.name.lower() for column in table.columns}
        role_columns = {name.lower() for name in table_options.get("columns", [])}
        masked_columns = {name.lower() for name in table_options.get("masked_columns", [])}
        writable_columns = role_columns - masked_columns if role_columns else schema_columns - masked_columns
        allowed_columns = role_columns or schema_columns
        for column in tree.find_all(exp.Column):
            if column.name.lower() not in schema_columns:
                raise ValidationError(f"Unknown column: {column.name}")
            if column.name.lower() not in allowed_columns or column.name.lower() in masked_columns:
                raise PermissionError(f"Role cannot use column '{column.name}'.")
        if isinstance(tree, exp.Update):
            if tree.args.get("from") or tree.args.get("joins") or tree.args.get("where") is None:
                raise ValidationError("UPDATE requires a WHERE clause and one target table.")
            for assignment in tree.expressions:
                column = assignment.this
                if not isinstance(column, exp.Column) or column.name.lower() not in writable_columns:
                    raise PermissionError(f"Role cannot update column '{column.name}'.")
        else:
            if not isinstance(tree.expression, exp.Values):
                raise ValidationError("INSERT must use literal VALUES, not INSERT ... SELECT.")
            insert_columns = tree.this.expressions if isinstance(tree.this, exp.Schema) else []
            if not insert_columns and writable_columns != schema_columns:
                raise ValidationError("INSERT must name columns when column permissions are configured.")
            for column in insert_columns:
                if column.name.lower() not in writable_columns:
                    raise PermissionError(f"Role cannot insert into column '{column.name}'.")
        if any(isinstance(node, exp.Subquery) for node in tree.walk()):
            raise ValidationError("Subqueries are not allowed in write previews.")
        return tree.sql(dialect=dialect)

    def mask_result(self, result: Result, role: RolePolicy, sql: str) -> Result:
        if not self.enabled or not role.tables:
            return result
        tree = sqlglot.parse_one(sql)
        table_aliases = {
            (table.alias_or_name or table.name).lower(): table.name.lower()
            for table in tree.find_all(exp.Table)
        }
        role_tables = {name.lower(): options for name, options in role.tables.items()}
        masked = set()
        for select in tree.find_all(exp.Select):
            for projection in select.expressions:
                column = projection.this if isinstance(projection, exp.Alias) else projection
                if not isinstance(column, exp.Column):
                    continue
                table_name = (
                    table_aliases.get(column.table.lower(), column.table.lower())
                    if column.table else None
                )
                applicable_tables = [table_name] if table_name else list(role.tables)
                if any(
                    column.name.lower() in {
                        item.lower() for item in role_tables.get(name.lower(), {}).get("masked_columns", [])
                    }
                    for name in applicable_tables
                ):
                    masked.add(projection.alias_or_name.lower())
        return Result(
            result.columns,
            [
                tuple("***" if column.lower() in masked else value
                      for column, value in zip(result.columns, row))
                for row in result.rows
            ],
            result.truncated,
        )


class AuditLog:
    """Append-only query metadata log; raw questions, SQL, and credentials are omitted."""

    _lock = threading.Lock()

    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get(
            "NLPSQL_AUDIT_DB",
            os.path.join(
                os.environ.get("LOCALAPPDATA") or str(Path.home()),
                "NLPSQL",
                "audit.sqlite3",
            ),
        )
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, sqlite3.connect(self.path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    role TEXT,
                    schema_name TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    question_hash TEXT,
                    sql_hash TEXT
                )"""
            )

    @staticmethod
    def _hash(value: str | None) -> str | None:
        return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None

    def record(
        self,
        role: str | None,
        schema_name: str,
        operation: str,
        outcome: str,
        question: str | None = None,
        sql: str | None = None,
    ) -> None:
        with self._lock, sqlite3.connect(self.path) as connection:
            connection.execute(
                """INSERT INTO audit_events
                   (role, schema_name, operation, outcome, question_hash, sql_hash)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    role,
                    schema_name,
                    operation,
                    outcome,
                    self._hash(question),
                    self._hash(sql),
                ),
            )
