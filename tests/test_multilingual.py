import pytest
from core.translator import is_likely_english, translate_query
from core.adapters.sqlite import SQLiteAdapter
from core.pipeline import Pipeline
from core.generator import MockGenerator

SAMPLE_SQL = """
CREATE TABLE employees (id INT PRIMARY KEY, name TEXT, salary INT);
INSERT INTO employees VALUES (1, 'Alice', 75000), (2, 'Bob', 90000);
"""


def test_is_likely_english_detection():
    # English queries
    assert is_likely_english("List all employees") is True
    assert is_likely_english("What is the average salary by department?") is True
    assert is_likely_english("Show active projects") is True

    # Non-English queries
    assert is_likely_english("Lista todos los empleados") is False
    assert is_likely_english("सभी कर्मचारियों की सूची दिखाएं") is False
    assert is_likely_english("Zeige alle Projekte") is False


def test_pipeline_multilingual_integration(monkeypatch):
    # Mock translation to avoid external network dependency in unit tests
    def mock_translate_query(text, timeout_seconds=4.0):
        if "empleados" in text:
            return "List all employees", True, "es"
        return text, False, "en"

    monkeypatch.setattr("core.pipeline.translate_query", mock_translate_query)

    adapter = SQLiteAdapter(seed_sql=SAMPLE_SQL)
    generator = MockGenerator(fallback_sql="SELECT * FROM employees;")
    pipeline = Pipeline(adapter=adapter, generator=generator)

    # Spanish query
    result = pipeline.ask("Lista todos los empleados")

    assert result.is_translated is True
    assert result.original_question == "Lista todos los empleados"
    assert result.question == "List all employees"
    assert result.sql == "SELECT * FROM employees;"
    assert result.result is not None
    assert len(result.result.rows) == 2
