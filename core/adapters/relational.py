import re
from .base import Column, ExecutionError, Result, Schema, Table


class RelationalAdapter:
    """Read-only PostgreSQL/MySQL adapter using SQLAlchemy and native drivers."""

    def __init__(self, connection_string: str):
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.engine import make_url
        except ImportError as error:
            raise ImportError(
                "PostgreSQL/MySQL connections require SQLAlchemy, psycopg, and PyMySQL."
            ) from error

        url = make_url(connection_string)
        if url.drivername in {"postgres", "postgresql"}:
            url = url.set(drivername="postgresql+psycopg")
        elif url.drivername == "mysql":
            url = url.set(drivername="mysql+pymysql")
        if url.drivername not in {"postgresql+psycopg", "mysql+pymysql"}:
            raise ValueError("Use a PostgreSQL or MySQL connection string.")
        self._password = url.password
        self.engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_timeout=5,
            connect_args={"connect_timeout": 5},
        )
        self._dialect = "postgres" if url.drivername.startswith("postgresql") else "mysql"

    def dialect(self) -> str:
        return self._dialect

    def get_schema(self) -> Schema:
        from sqlalchemy import inspect

        inspector = inspect(self.engine)
        tables = []
        for table_name in inspector.get_table_names():
            columns = [
                Column(
                    name=item["name"],
                    type=str(item["type"]),
                    is_pk=item["name"] in inspector.get_pk_constraint(table_name).get("constrained_columns", []),
                )
                for item in inspector.get_columns(table_name)
            ]
            foreign_keys = [
                (item["constrained_columns"][0], item["referred_table"], item["referred_columns"][0])
                for item in inspector.get_foreign_keys(table_name)
                if len(item["constrained_columns"]) == 1 and len(item["referred_columns"]) == 1
            ]
            tables.append(Table(table_name, columns, foreign_keys))
        return Schema(tables)

    def execute_readonly(
        self,
        sql: str,
        timeout_s: float = 5.0,
        limit: int = 100,
    ) -> Result:
        raw_connection = self.engine.raw_connection()
        cursor = raw_connection.cursor()
        try:
            if self._dialect == "postgres":
                cursor.execute("BEGIN READ ONLY")
                timeout_ms = max(1, int(timeout_s * 1000))
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{timeout_ms}ms",),
                )
            else:
                cursor.execute("START TRANSACTION READ ONLY")
            cursor.execute(sql)
            rows = cursor.fetchmany(limit + 1) if cursor.description else []
            columns = [column[0] for column in cursor.description] if cursor.description else []
            return Result(columns, rows[:limit], truncated=len(rows) > limit)
        except Exception as error:
            raise ExecutionError(self._redact(str(error))) from error
        finally:
            try:
                raw_connection.rollback()
            finally:
                cursor.close()
                raw_connection.close()

    def close(self):
        self.engine.dispose()

    def _redact(self, message: str) -> str:
        if self._password:
            message = message.replace(self._password, "***")
        return re.sub(r"(://[^:/@]+:)[^@]+@", r"\1***@", message)
