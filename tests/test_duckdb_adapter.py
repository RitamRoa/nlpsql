import pytest
from core.adapters.duckdb import DuckDBAdapter
from core.adapters.base import ExecutionError
from core.pipeline import Pipeline

SAMPLE_DUCKDB_SEED_SQL = """
CREATE TABLE departments (
    id INT PRIMARY KEY,
    name VARCHAR
);

CREATE TABLE employees (
    id INT PRIMARY KEY,
    name VARCHAR,
    dept_id INT,
    salary INT,
    FOREIGN KEY (dept_id) REFERENCES departments(id)
);

INSERT INTO departments VALUES (10, 'Sales'), (20, 'Engineering');
INSERT INTO employees VALUES (1, 'Alice', 10, 80000);
INSERT INTO employees VALUES (2, 'Bob', 20, 95000);
INSERT INTO employees VALUES (3, 'Charlie', 10, 60000);
"""


def test_duckdb_get_schema():
    adapter = DuckDBAdapter(seed_sql=SAMPLE_DUCKDB_SEED_SQL)
    schema = adapter.get_schema()
    table_names = {t.name for t in schema.tables}
    assert "employees" in table_names
    assert "departments" in table_names

    emp_table = next(t for t in schema.tables if t.name == "employees")
    col_names = [c.name for c in emp_table.columns]
    assert "id" in col_names
    assert "name" in col_names
    assert "salary" in col_names

    # Check primary key detection
    id_col = next(c for c in emp_table.columns if c.name == "id")
    assert id_col.is_pk is True

    # Check foreign keys
    assert len(emp_table.foreign_keys) == 1
    assert emp_table.foreign_keys[0] == ("dept_id", "departments", "id")
    adapter.close()


def test_duckdb_execute_readonly_safety():
    adapter = DuckDBAdapter(seed_sql=SAMPLE_DUCKDB_SEED_SQL)
    # Direct drop or insert should raise ExecutionError
    with pytest.raises(ExecutionError):
        adapter.execute_readonly("DROP TABLE employees")

    with pytest.raises(ExecutionError):
        adapter.execute_readonly("INSERT INTO employees VALUES (4, 'David', 10, 70000)")
    adapter.close()


def test_duckdb_limit_truncation():
    adapter = DuckDBAdapter(seed_sql=SAMPLE_DUCKDB_SEED_SQL)
    result = adapter.execute_readonly("SELECT * FROM employees", limit=2)
    assert len(result.rows) == 2
    assert result.truncated is True

    result_all = adapter.execute_readonly("SELECT * FROM employees", limit=10)
    assert len(result_all.rows) == 3
    assert result_all.truncated is False
    adapter.close()


class MockDuckGenerator:
    def generate(self, question, schema_ddl, feedback=None):
        return "SELECT name, salary FROM employees WHERE salary > 70000 ORDER BY salary DESC;"


def test_duckdb_in_pipeline():
    adapter = DuckDBAdapter(seed_sql=SAMPLE_DUCKDB_SEED_SQL)
    generator = MockDuckGenerator()
    pipeline = Pipeline(adapter=adapter, generator=generator)

    res = pipeline.ask("List employees making over 70k")
    assert res.error is None
    assert res.attempts == 1
    assert res.result is not None
    assert len(res.result.rows) == 2
    assert res.result.rows[0][0] == "Bob"
    adapter.close()
