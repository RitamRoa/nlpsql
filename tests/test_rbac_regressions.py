"""Access-control regressions that must hold on every supported SQL engine.

The CTE-shadowing cases failed on DuckDB before the fix: a CTE named like a hidden
table made the policy layer skip its checks, while the engine resolved the inner
reference to the real table.
"""
import pytest

from core.adapters.duckdb import DuckDBAdapter
from core.adapters.sqlite import SQLiteAdapter
from core.security import AccessPolicy, RolePolicy
from core.validator import ValidationError

SEED = """
CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    name TEXT,
    department TEXT,
    salary INTEGER,
    hire_date TEXT
);
CREATE TABLE sales (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER,
    amount INTEGER
);
INSERT INTO employees VALUES
    (1, 'Alice', 'Sales', 50000, '2020-01-01'),
    (2, 'Bob', 'Engineering', 70000, '2021-02-02'),
    (3, 'Cara', 'Sales', 65000, '2022-03-03');
INSERT INTO sales VALUES (1, 1, 100), (2, 3, 250);
"""

ADAPTERS = [
    pytest.param((lambda: SQLiteAdapter(seed_sql=SEED), "sqlite"), id="sqlite"),
    pytest.param((lambda: DuckDBAdapter(seed_sql=SEED), "duckdb"), id="duckdb"),
]


@pytest.fixture(params=ADAPTERS)
def env(request):
    factory, dialect = request.param
    adapter = factory()
    full = adapter.get_schema()
    policy = AccessPolicy(
        enabled=True,
        roles={
            "analyst": RolePolicy(
                schemas=["company"],
                tables={
                    "employees": {
                        "columns": ["id", "name", "department", "salary"],
                        "masked_columns": ["salary"],
                        "row_filter": "department = 'Sales'",
                    }
                },
            )
        },
    )
    role = policy.role("analyst")
    visible = policy.restrict_schema(full, role)
    yield adapter, policy, role, visible, full, dialect
    adapter.close()


def _run(env, sql):
    adapter, policy, role, visible, full, dialect = env
    guarded = policy.enforce_query(sql, visible, role, dialect, full_schema=full)
    return policy.mask_result(adapter.execute_readonly(guarded), role, guarded)


def test_row_filter_is_applied(env):
    result = _run(env, "SELECT name FROM employees ORDER BY name")
    assert [row[0] for row in result.rows] == ["Alice", "Cara"]


def test_masked_column_is_masked(env):
    result = _run(env, "SELECT name, salary FROM employees")
    assert {row[1] for row in result.rows} == {"***"}


def test_hidden_table_is_blocked(env):
    with pytest.raises((ValidationError, PermissionError)):
        _run(env, "SELECT * FROM sales")


def test_hidden_column_is_blocked(env):
    with pytest.raises((ValidationError, PermissionError)):
        _run(env, "SELECT hire_date FROM employees")


@pytest.mark.parametrize("sql", [
    "SELECT name FROM employees WHERE salary > 1000",
    "SELECT MAX(salary) FROM employees",
    "SELECT name FROM employees ORDER BY salary",
])
def test_masked_column_cannot_be_used_to_infer_values(env, sql):
    with pytest.raises(PermissionError, match="masked column"):
        _run(env, sql)


@pytest.mark.parametrize("sql", [
    # CTE named like a table the role cannot see.
    "WITH sales AS (SELECT * FROM sales) SELECT * FROM sales",
    # CTE named like a table the role can see: would bypass row filter and masking.
    "WITH employees AS (SELECT * FROM employees) SELECT name, salary FROM employees",
    "WITH Employees AS (SELECT id, name, department, salary FROM employees) "
    "SELECT name, salary FROM Employees",
    # Same trick nested in a subquery.
    "SELECT name FROM (WITH employees AS (SELECT * FROM employees) SELECT name FROM employees) t",
])
def test_cte_cannot_shadow_a_real_table(env, sql):
    with pytest.raises((ValidationError, PermissionError)):
        _run(env, sql)


def test_unrelated_cte_still_works_and_keeps_filter(env):
    result = _run(
        env,
        "WITH dept AS (SELECT name, department FROM employees) "
        "SELECT name FROM dept ORDER BY name",
    )
    assert [row[0] for row in result.rows] == ["Alice", "Cara"]
