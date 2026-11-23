import sqlite3
import time
from collections import Counter
from .base import Column, Table, Schema, Result, ExecutionError


class SQLiteAdapter:
    def __init__(
        self,
        path: str = ":memory:",
        seed_sql: str | None = None,
        foreign_key_overrides: list[tuple[str, str, str, str]] | None = None,
    ):
        self.foreign_key_overrides = foreign_key_overrides or []
        self._supports_approved_writes = path == ":memory:"
        if path == ":memory:":
            self.conn = sqlite3.connect(path, check_same_thread=False)
            if seed_sql:
                self.conn.executescript(seed_sql)
        else:
            self.conn = sqlite3.connect(
                f"file:{path}?mode=ro", uri=True, check_same_thread=False
            )
        self.conn.execute("PRAGMA query_only = ON")

    def close(self):
        """Explicitly close the underlying connection (important on Windows)."""
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def dialect(self) -> str:
        return "sqlite"

    def get_schema(self) -> Schema:
        names = [
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        tables = []
        for t in names:
            cols = [
                Column(r[1], r[2], bool(r[5]))
                for r in self.conn.execute(f'PRAGMA table_info("{t}")')
            ]
            fks = [
                (r[3], r[2], r[4])
                for r in self.conn.execute(f'PRAGMA foreign_key_list("{t}")')
            ]
            fks.extend(
                (column, ref_table, ref_column)
                for table, column, ref_table, ref_column in self.foreign_key_overrides
                if table == t and (column, ref_table, ref_column) not in fks
            )
            tables.append(Table(t, cols, fks))
        return Schema(tables)

    def execute_readonly(
        self, sql: str, timeout_s: float = 5.0, limit: int = 100
    ) -> Result:
        deadline = time.monotonic() + timeout_s
        self.conn.set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0, 10000
        )
        try:
            cur = self.conn.execute(sql)
            rows = cur.fetchmany(limit + 1)
            cols = [d[0] for d in cur.description] if cur.description else []
        except sqlite3.Error as e:
            raise ExecutionError(str(e)) from e
        finally:
            self.conn.set_progress_handler(None, 0)
        return Result(cols, rows[:limit], truncated=len(rows) > limit)

    def approved_write(self, sql: str, commit: bool = False) -> dict:
        """Run an already-authorized INSERT/UPDATE and return a bounded row diff."""
        if not self._supports_approved_writes:
            raise PermissionError("Approval-based writes are available only for in-memory SQLite schemas.")
        if self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'trigger' LIMIT 1"
        ).fetchone():
            raise PermissionError("Approval-based writes are disabled when database triggers exist.")
        from sqlglot import exp, parse_one

        statement = parse_one(sql, read="sqlite")
        target = statement.this.this if isinstance(statement, exp.Insert) and isinstance(statement.this, exp.Schema) else statement.this
        if not isinstance(target, exp.Table):
            raise ExecutionError("Could not identify the write target table.")
        table_name = target.name
        for (other_table,) in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ):
            if other_table.lower() == table_name.lower():
                continue
            references = self.conn.execute(
                f'PRAGMA foreign_key_list("{other_table.replace(chr(34), chr(34) * 2)}")'
            ).fetchall()
            if any(row[2].lower() == table_name.lower() for row in references):
                raise PermissionError(
                    "Approval-based writes are disabled for tables referenced by foreign keys."
                )
        quoted_table = '"' + table_name.replace('"', '""') + '"'
        before_rows = self.conn.execute(f"SELECT * FROM {quoted_table} LIMIT 1001").fetchall()
        if len(before_rows) > 1000:
            raise ExecutionError("Write preview is limited to tables with at most 1000 rows.")
        names = [row[1] for row in self.conn.execute(f"PRAGMA table_info({quoted_table})")]
        self.conn.execute("PRAGMA query_only = OFF")
        try:
            self.conn.execute("BEGIN")
            cursor = self.conn.execute(sql)
            affected_rows = max(cursor.rowcount, 0)
            after_rows = self.conn.execute(f"SELECT * FROM {quoted_table} LIMIT 1001").fetchall()
            if len(after_rows) > 1000:
                raise ExecutionError("Write preview would exceed the 1000-row safety limit.")
            before_counts = Counter(tuple(row) for row in before_rows)
            after_counts = Counter(tuple(row) for row in after_rows)
            removed = list((before_counts - after_counts).elements())[:100]
            added = list((after_counts - before_counts).elements())[:100]
            preview = {
                "table": table_name,
                "columns": names,
                "before": [list(row) for row in removed],
                "after": [list(row) for row in added],
                "affected_rows": affected_rows,
                "truncated": sum((before_counts - after_counts).values()) > 100
                    or sum((after_counts - before_counts).values()) > 100,
            }
            if commit:
                self.conn.commit()
            else:
                self.conn.rollback()
            return preview
        except ExecutionError:
            self.conn.rollback()
            raise
        except sqlite3.Error as error:
            self.conn.rollback()
            raise ExecutionError(str(error)) from error
        finally:
            self.conn.execute("PRAGMA query_only = ON")
