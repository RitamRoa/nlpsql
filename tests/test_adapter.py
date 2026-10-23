import pytest
from core.adapters.sqlite import SQLiteAdapter
from core.adapters.base import ExecutionError

SAMPLE_SEED_SQL = """
CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    name TEXT,
    department TEXT,
    salary INTEGER,
    hire_date TEXT
);

CREATE TABLE departments (
    id INTEGER PRIMARY KEY,
    name TEXT,
    manager_id INTEGER
);

CREATE TABLE sales (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER,
    amount INTEGER,
    sale_date TEXT,
    FOREIGN KEY (employee_id) REFERENCES employees(id)
);

INSERT INTO employees VALUES (1, 'Alice Smith', 'Sales', 75000, '2023-01-15');
INSERT INTO employees VALUES (2, 'Bob Jones', 'Engineering', 90000, '2022-06-20');
INSERT INTO employees VALUES (3, 'Charlie Brown', 'Sales', 60000, '2024-03-10');
INSERT INTO employees VALUES (4, 'Diana Prince', 'Engineering', 110000, '2021-11-05');
INSERT INTO employees VALUES (5, 'Evan Wright', 'Marketing', 65000, '2023-08-25');

INSERT INTO departments VALUES (10, 'Sales', 1);
INSERT INTO departments VALUES (20, 'Engineering', 4);
INSERT INTO departments VALUES (30, 'Marketing', 5);

INSERT INTO sales VALUES (101, 1, 1500, '2024-01-10');
"""


def test_get_schema_returns_three_tables():
    adapter = SQLiteAdapter(seed_sql=SAMPLE_SEED_SQL)
    schema = adapter.get_schema()
    table_names = {t.name for t in schema.tables}
    assert table_names == {"employees", "departments", "sales"}

    emp_table = next(t for t in schema.tables if t.name == "employees")
    col_names = [c.name for c in emp_table.columns]
    assert "name" in col_names
    assert "salary" in col_names


def test_drop_table_raises_execution_error():
    adapter = SQLiteAdapter(seed_sql=SAMPLE_SEED_SQL)
    with pytest.raises(ExecutionError):
        adapter.execute_readonly("DROP TABLE employees")


def test_select_limit_truncation():
    adapter = SQLiteAdapter(seed_sql=SAMPLE_SEED_SQL)
    result = adapter.execute_readonly("SELECT * FROM employees", limit=2)
    assert len(result.rows) == 2
    assert result.truncated is True

    result_all = adapter.execute_readonly("SELECT * FROM employees", limit=10)
    assert len(result_all.rows) == 5
    assert result_all.truncated is False
