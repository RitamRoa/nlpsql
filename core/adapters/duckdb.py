import os
import re
import tempfile
import duckdb
from .base import Column, Table, Schema, Result, ExecutionError


class DuckDBAdapter:
    """Pluggable database adapter for DuckDB supporting read-only safety,

    schema reflection, and query execution.
    """

    def __init__(self, path: str = ":memory:", seed_sql: str | None = None):
        self._temp_dir = None
        if path == ":memory:":
            self._temp_dir = tempfile.TemporaryDirectory()
            db_path = os.path.join(self._temp_dir.name, "duckdb_store.db")
            if seed_sql:
                seed_conn = duckdb.connect(db_path)
                seed_conn.execute(seed_sql)
                seed_conn.close()
            else:
                seed_conn = duckdb.connect(db_path)
                seed_conn.close()
            self.conn = duckdb.connect(db_path, read_only=True)
        else:
            if seed_sql and not os.path.exists(path):
                seed_conn = duckdb.connect(path)
                seed_conn.execute(seed_sql)
                seed_conn.close()
            self.conn = duckdb.connect(path, read_only=True)

    def dialect(self) -> str:
        return "duckdb"

    def get_schema(self) -> Schema:
        table_rows = self.conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        table_names = [r[0] for r in table_rows]

        # Extract primary keys
        try:
            pk_rows = self.conn.execute("""
                SELECT table_name, unnest(constraint_column_names) as col
                FROM duckdb_constraints()
                WHERE constraint_type = 'PRIMARY KEY'
            """).fetchall()
            primary_keys = {(r[0], r[1]) for r in pk_rows}
        except Exception:
            primary_keys = set()

        # Extract foreign keys
        fks_by_table: dict[str, list[tuple[str, str, str]]] = {
            t: [] for t in table_names
        }
        try:
            fk_rows = self.conn.execute("""
                SELECT table_name, constraint_text
                FROM duckdb_constraints()
                WHERE constraint_type = 'FOREIGN KEY'
            """).fetchall()
            fk_pattern = re.compile(
                r"FOREIGN KEY\s*\((.*?)\)\s*REFERENCES\s*([^(]+)\s*\((.*?)\)",
                re.IGNORECASE,
            )
            for t_name, c_text in fk_rows:
                m = fk_pattern.search(c_text)
                if m:
                    col = m.group(1).strip()
                    ref_tbl = m.group(2).strip()
                    ref_col = m.group(3).strip()
                    if t_name in fks_by_table:
                        fks_by_table[t_name].append((col, ref_tbl, ref_col))
        except Exception:
            pass

        # Extract column details
        col_rows = self.conn.execute("""
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'main'
            ORDER BY table_name, ordinal_position
        """).fetchall()

        cols_by_table: dict[str, list[Column]] = {t: [] for t in table_names}
        for t_name, c_name, d_type in col_rows:
            if t_name in cols_by_table:
                is_pk = (t_name, c_name) in primary_keys
                cols_by_table[t_name].append(
                    Column(name=c_name, type=d_type, is_pk=is_pk)
                )

        tables = [
            Table(name=t, columns=cols_by_table[t], foreign_keys=fks_by_table[t])
            for t in table_names
        ]
        return Schema(tables=tables)

    def execute_readonly(
        self, sql: str, timeout_s: float = 5.0, limit: int = 100
    ) -> Result:
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchmany(limit + 1)
            cols = [d[0] for d in cursor.description] if cursor.description else []
            truncated = len(rows) > limit
            return Result(columns=cols, rows=rows[:limit], truncated=truncated)
        except Exception as e:
            raise ExecutionError(str(e)) from e

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
        if self._temp_dir:
            self._temp_dir.cleanup()
