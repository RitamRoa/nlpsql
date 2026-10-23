import sqlite3
import time
from .base import Column, Table, Schema, Result, ExecutionError


class SQLiteAdapter:
    def __init__(self, path: str = ":memory:", seed_sql: str | None = None):
        if path == ":memory:":
            self.conn = sqlite3.connect(path, check_same_thread=False)
            if seed_sql:
                self.conn.executescript(seed_sql)
        else:
            self.conn = sqlite3.connect(
                f"file:{path}?mode=ro", uri=True, check_same_thread=False
            )
        self.conn.execute("PRAGMA query_only = ON")

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
