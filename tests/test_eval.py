from core.adapters.base import Result
from core.adapters.sqlite import SQLiteAdapter
from eval.metrics import compare_results, EvalMetrics
from eval.runner import EvalRunner, run_ablation_study

SEED_SQL = """
CREATE TABLE employees (id INT PRIMARY KEY, name TEXT, salary INT);
INSERT INTO employees VALUES (1, 'Alice', 75000), (2, 'Bob', 90000), (3, 'Charlie', 60000);
"""


def test_compare_results_order_invariance():
    r1 = Result(columns=["name", "salary"], rows=[("Alice", 75000), ("Bob", 90000)])
    # Different column names and different row order, but same content
    r2 = Result(
        columns=["emp_name", "sal"], rows=[("Bob", 90000), ("Alice", 75000)]
    )

    assert compare_results(r1, r2, order_sensitive=False) is True
    assert compare_results(r1, r2, order_sensitive=True) is False


def test_compare_results_numeric_and_null_handling():
    r1 = Result(columns=["val"], rows=[(10.0,), (None,)])
    r2 = Result(columns=["v"], rows=[(10.00001,), (None,)])
    # Floating point rounding should match
    assert compare_results(r1, r2) is True

    r3 = Result(columns=["val"], rows=[(10.5,), (None,)])
    assert compare_results(r1, r3) is False


class MockEvalGenerator:
    """Mock generator simulating:

    Q1: exact match
    Q2: first attempt has invalid table, second attempt fixes it
    Q3: unfixable syntax error
    """

    def __init__(self):
        self.attempts_by_q = {}

    def generate(self, question, schema_ddl, feedback=None):
        if "Alice" in question:
            return "SELECT name FROM employees WHERE name = 'Alice';"
        elif "salary" in question:
            # First attempt fails schema validation
            if feedback is None:
                return "SELECT salary FROM emp_table;"
            else:
                return "SELECT salary FROM employees ORDER BY salary DESC;"
        else:
            return "INVALID SQL SYNTAX;"


def test_eval_runner_accuracy_and_repair():
    adapter = SQLiteAdapter(seed_sql=SEED_SQL)
    generator = MockEvalGenerator()
    from core.pipeline import Pipeline

    pipeline = Pipeline(adapter=adapter, generator=generator, max_repairs=1)
    runner = EvalRunner(pipeline=pipeline, adapter=adapter)

    dataset = [
        {"question": "Find Alice", "query": "SELECT name FROM employees WHERE name = 'Alice';"},
        {"question": "List salary", "query": "SELECT salary FROM employees ORDER BY salary DESC;"},
        {"question": "Unknown task", "query": "SELECT * FROM employees;"},
    ]

    report = runner.evaluate_dataset(dataset)
    m = report.metrics

    assert m.total_examples == 3
    # Q1 succeeds directly, Q2 succeeds after repair, Q3 fails
    assert m.valid_sql_count == 2
    assert m.exec_accuracy_count == 2
    assert m.repaired_success_count == 1
    assert m.exec_accuracy == 66.66666666666666 or round(m.exec_accuracy, 1) == 66.7
    assert "Benchmark Report" in report.summary()


def test_ablation_study_runner():
    adapter = SQLiteAdapter(seed_sql=SEED_SQL)
    generator = MockEvalGenerator()

    dataset = [
        {"question": "Find Alice", "query": "SELECT name FROM employees WHERE name = 'Alice';"},
        {"question": "List salary", "query": "SELECT salary FROM employees ORDER BY salary DESC;"},
    ]

    ablations = run_ablation_study(dataset, adapter=adapter, generator=generator)

    baseline = ablations["baseline_single_shot"].metrics
    repaired = ablations["with_self_repair"].metrics

    # In baseline, Q2 fails because max_repairs=0
    assert baseline.exec_accuracy_count == 1
    # In repaired, Q2 succeeds because max_repairs=1
    assert repaired.exec_accuracy_count == 2
    assert repaired.repaired_success_count == 1
