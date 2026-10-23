from core.adapters.sqlite import SQLiteAdapter
from core.pipeline import Pipeline, schema_to_ddl

SAMPLE_SEED_SQL = """
CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    name TEXT,
    department TEXT,
    salary INTEGER,
    hire_date TEXT
);
INSERT INTO employees VALUES (1, 'Alice Smith', 'Sales', 75000, '2023-01-15');
"""


class MockGenerator:
    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0
        self.last_feedback = None

    def generate(self, question, schema_ddl, feedback=None):
        self.last_feedback = feedback
        resp = self.responses[min(self.call_count, len(self.responses) - 1)]
        self.call_count += 1
        return resp


def test_pipeline_successful_run():
    adapter = SQLiteAdapter(seed_sql=SAMPLE_SEED_SQL)
    generator = MockGenerator(["SELECT name FROM employees;"])
    pipeline = Pipeline(adapter, generator)

    res = pipeline.ask("List employee names")
    assert res.error is None
    assert res.attempts == 1
    assert res.result is not None
    assert res.result.rows == [("Alice Smith",)]
    assert "generate_0" in res.timings
    assert "execute_0" in res.timings


def test_pipeline_one_shot_repair_success():
    adapter = SQLiteAdapter(seed_sql=SAMPLE_SEED_SQL)
    # 1st attempt generates invalid table name 'staff', 2nd attempt fixes it to 'employees'
    generator = MockGenerator(
        ["SELECT name FROM staff;", "SELECT name FROM employees;"]
    )
    pipeline = Pipeline(adapter, generator, max_repairs=1)

    res = pipeline.ask("List employee names")
    assert res.error is None
    assert res.attempts == 2
    assert res.result is not None
    assert res.result.rows == [("Alice Smith",)]
    assert generator.last_feedback is not None
    assert "unknown table: staff" in generator.last_feedback


def test_pipeline_repair_exhausted():
    adapter = SQLiteAdapter(seed_sql=SAMPLE_SEED_SQL)
    # Always generates invalid SQL
    generator = MockGenerator(["DROP TABLE employees;", "DROP TABLE employees;"])
    pipeline = Pipeline(adapter, generator, max_repairs=1)

    res = pipeline.ask("Delete employees")
    assert res.error is not None
    assert "only SELECT queries are allowed" in res.error or "write operations are not allowed" in res.error
    assert res.attempts == 2
    assert res.result is None
