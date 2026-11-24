import time

import pytest

from core.adapters.duckdb import DuckDBAdapter
from core.adapters.base import ExecutionError
from core.loader import create_adapter
from core.validator import ValidationError, validate


@pytest.fixture(scope="module")
def schema():
    adapter = create_adapter("sqlite", schema_name="company")
    yield adapter.get_schema()
    adapter.close()


@pytest.mark.parametrize("sql", [
    "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r) SELECT n FROM r",
    "SELECT 1 FROM employees a, employees b, employees c",
    "SELECT 1 FROM employees a CROSS JOIN employees b CROSS JOIN employees c",
    "SELECT e.not_a_column FROM employees e",
    "SELECT employees.not_a_column FROM employees",
    "SELECT pg_sleep(100)",
])
def test_rejects(sql, schema):
    with pytest.raises(ValidationError):
        validate(sql, schema, "sqlite")


@pytest.mark.parametrize("sql", [
    "SELECT e.name FROM employees e JOIN departments d ON e.department = d.name",
    "SELECT a.name, b.name FROM employees a, employees b WHERE a.id < b.id",
    "WITH t AS (SELECT name AS n FROM employees) SELECT t.n FROM t",
    "SELECT e.name FROM employees e WHERE e.id IN (SELECT s.employee_id FROM sales s)",
])
def test_still_accepts_legitimate_queries(sql, schema):
    validate(sql, schema, "sqlite")


def test_duckdb_query_is_interrupted_at_the_timeout():
    adapter = DuckDBAdapter(seed_sql="CREATE TABLE t(a INTEGER); INSERT INTO t VALUES (1);")
    start = time.perf_counter()
    with pytest.raises(ExecutionError, match="time limit"):
        adapter.execute_readonly("SELECT count(*) FROM range(1000000000000) a, range(1000000) b", timeout_s=0.5)
    assert time.perf_counter() - start < 5
    assert adapter.execute_readonly("SELECT a FROM t").rows == [(1,)]
    adapter.close()
