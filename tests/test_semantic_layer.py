import pytest

from core.adapters.base import Column, Schema, Table
from core.adapters.sqlite import SQLiteAdapter
from core.knowledge import DomainKnowledgeEngine
from core.loader import build_pipeline
from core.retriever import SchemaRetriever
from core.semantic_layer import (
    ColumnDefinition,
    MetricDefinition,
    RuleDefinition,
    SemanticLayer,
    TableDefinition,
    generate_semantic_layer,
    load_semantic_layer,
    write_semantic_layer,
)


def test_generated_semantic_layer_round_trips_and_preserves_edits(tmp_path):
    schema = Schema(tables=[
        Table(
            name="employees",
            columns=[
                Column("id", "INTEGER", is_pk=True),
                Column("salary", "REAL"),
            ],
        )
    ])
    existing = SemanticLayer(tables={
        "employees": TableDefinition(
            description="People employed by the organization.",
            columns={
                "salary": ColumnDefinition(
                    description="Annual base compensation.",
                    synonyms=["compensation"],
                )
            },
        )
    })

    generated = generate_semantic_layer(schema, existing)
    output = tmp_path / "semantic.yaml"
    write_semantic_layer(generated, output)
    loaded = load_semantic_layer(output)
    loaded.validate_against(schema)

    assert loaded.tables["employees"].description == "People employed by the organization."
    assert loaded.tables["employees"].columns["salary"].synonyms == ["compensation"]
    assert any("SUM(" in metric.expression for metric in loaded.metrics)
    assert any("AVG(" in metric.expression for metric in loaded.metrics)


def test_semantic_aliases_affect_schema_retrieval():
    schema = Schema(tables=[
        Table("employees", [Column("id", "INTEGER", is_pk=True), Column("salary", "REAL")]),
        Table("products", [Column("id", "INTEGER", is_pk=True), Column("price", "REAL")]),
    ])
    layer = SemanticLayer(tables={
        "employees": TableDefinition(
            synonyms=["staff"],
            columns={"salary": ColumnDefinition(synonyms=["compensation"])},
        )
    })

    retrieved = SchemaRetriever(max_tables=1, semantic_layer=layer).retrieve(
        "show compensation for staff", schema
    )

    assert [table.name for table in retrieved.tables] == ["employees"]


def test_metric_matching_requires_a_metric_phrase():
    adapter = SQLiteAdapter(seed_sql=(
        "CREATE TABLE employees (id INTEGER PRIMARY KEY, salary REAL);"
    ))
    layer = SemanticLayer(metrics=[
        MetricDefinition(
            name="employees_salary_average",
            description="Average salary.",
            expression='AVG("employees"."salary")',
            synonyms=["average salary", "mean salary"],
        ),
    ])
    try:
        engine = DomainKnowledgeEngine(adapter, semantic_layer=layer)
        assert not engine.resolve("List employees with salary").semantic_definitions
        assert any(
            item.startswith("Metric employees_salary_average")
            for item in engine.resolve("Find average salary").semantic_definitions
        )
    finally:
        adapter.close()


def test_semantic_rules_and_metrics_hide_restricted_columns():
    adapter = SQLiteAdapter(seed_sql=(
        "CREATE TABLE employees (id INTEGER PRIMARY KEY, salary REAL);"
    ))
    layer = SemanticLayer(
        metrics=[
            MetricDefinition(
                name="salary_average",
                description="Average salary.",
                expression='AVG("employees"."salary")',
                synonyms=["average salary"],
            ),
        ],
        business_rules=[
            RuleDefinition(
                name="High Earner",
                description="High salary employees.",
                sql_expression="salary >= 90000",
                keywords=["high earners"],
            ),
        ],
    )
    try:
        engine = DomainKnowledgeEngine(adapter, semantic_layer=layer)
        visible_schema = Schema(tables=[
            Table("employees", [Column("id", "INTEGER", is_pk=True)])
        ])
        context = engine.resolve(
            "Show high earners and average salary",
            allowed_schema=visible_schema,
        )
        assert not context.matched_rules
        assert not context.semantic_definitions
    finally:
        adapter.close()


def test_builtin_business_rules_are_loaded_from_yaml():
    pipeline, adapter, knowledge = build_pipeline(schema_name="healthcare")
    try:
        context = knowledge.resolve("Show currently admitted patients")
        assert [rule.name for rule in context.matched_rules] == ["Current Inpatients"]
        result = pipeline.ask("Show all patients currently admitted")
        assert result.error is None
        assert result.knowledge_context is not None
        assert [rule.name for rule in result.knowledge_context.matched_rules] == [
            "Current Inpatients"
        ]
    finally:
        adapter.close()


def test_semantic_layer_rejects_unknown_schema_columns(tmp_path):
    path = tmp_path / "semantic.yaml"
    path.write_text(
        "version: 1\ntables:\n  employees:\n    columns:\n      wage:\n"
        "        description: Employee pay\n",
        encoding="utf-8",
    )
    layer = load_semantic_layer(path)
    schema = Schema(tables=[Table("employees", [Column("salary", "REAL")])])

    with pytest.raises(ValueError, match="employees.wage"):
        layer.validate_against(schema)
