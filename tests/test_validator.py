import pytest
from core.adapters.base import Schema, Table, Column
from core.validator import validate, ValidationError

SCHEMA = Schema(
    tables=[
        Table(
            "employees",
            [
                Column("id", "INTEGER", True),
                Column("name", "TEXT"),
                Column("dept_id", "INTEGER"),
            ],
        ),
        Table(
            "departments",
            [
                Column("id", "INTEGER", True),
                Column("name", "TEXT"),
            ],
        ),
    ]
)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM employees",
        "SELECT e.name FROM employees e JOIN departments d ON e.dept_id = d.id WHERE d.name = 'Sales'",
        "WITH s AS (SELECT dept_id FROM employees) SELECT dept_id FROM s",
    ],
)
def test_allows_valid(sql):
    validate(sql, SCHEMA)


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE employees",
        "DELETE FROM employees",
        "SELECT 1; DROP TABLE employees",
        "SELECT name FROM users",
        "SELECT salary FROM employees",
        "PRAGMA writable_schema = 1",
        "ATTACH DATABASE 'x.db' AS x",
        "SELECT load_extension('evil')",
    ],
)
def test_rejects_unsafe_or_invalid(sql):
    with pytest.raises(ValidationError):
        validate(sql, SCHEMA)
