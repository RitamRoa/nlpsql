# core.adapters package
from .base import Column, Table, Schema, Result, ExecutionError, DBAdapter
from .sqlite import SQLiteAdapter
from .duckdb import DuckDBAdapter

__all__ = [
    "Column",
    "Table",
    "Schema",
    "Result",
    "ExecutionError",
    "DBAdapter",
    "SQLiteAdapter",
    "DuckDBAdapter",
]
