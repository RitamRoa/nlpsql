import pytest

from core.agents.drafter import clean_sql


def test_plain_and_fenced_queries():
    assert clean_sql("SELECT name FROM employees") == "SELECT name FROM employees;"
    assert clean_sql("```sql\nSELECT name FROM employees;\n```") == "SELECT name FROM employees;"


def test_explanation_before_the_query_is_dropped():
    text = (
        "To fix the error, we need to qualify the column.\n"
        "Here's the corrected SQL:\n\n```sql\nSELECT AVG(consultation_fee) FROM doctors;\n```\nThis works."
    )
    assert clean_sql(text) == "SELECT AVG(consultation_fee) FROM doctors;"


def test_cte_is_not_cut_down_to_its_inner_select():
    sql = "WITH t AS (SELECT a FROM b) SELECT a FROM t;"
    assert clean_sql(sql) == sql
    assert clean_sql("Here you go:\n" + sql + "\nDone.") == sql


def test_semicolon_inside_a_string_does_not_end_the_query():
    assert clean_sql("SELECT 'a;b' FROM t; SELECT 2;") == "SELECT 'a;b' FROM t;"


def test_prose_word_select_mid_sentence_does_not_start_the_query():
    text = "We need to select the right table.\nSELECT name FROM employees;"
    assert clean_sql(text) == "SELECT name FROM employees;"


def test_answer_without_a_query_is_left_for_the_validator_to_reject():
    from core.adapters.base import Schema
    from core.validator import ValidationError, validate

    sql = clean_sql("Here's the corrected SQL:")
    with pytest.raises(ValidationError):
        validate(sql, Schema(tables=[]), "sqlite")
