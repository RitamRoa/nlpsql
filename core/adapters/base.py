from dataclasses import dataclass, field
from typing import Protocol


class ExecutionError(Exception):
    pass


@dataclass
class Column:
    name: str
    type: str
    is_pk: bool = False


@dataclass
class Table:
    name: str
    columns: list[Column]
    foreign_keys: list[tuple[str, str, str]] = field(default_factory=list)  # (col, ref_table, ref_col)


@dataclass
class Schema:
    tables: list[Table]


@dataclass
class Result:
    columns: list[str]
    rows: list[tuple]
    truncated: bool = False


class DBAdapter(Protocol):
    def get_schema(self) -> Schema: ...
    def execute_readonly(self, sql: str, timeout_s: float = 5.0, limit: int = 100) -> Result: ...
    def dialect(self) -> str: ...
