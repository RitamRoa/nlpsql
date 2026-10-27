from core.adapters.sqlite import SQLiteAdapter
from core.knowledge import DomainKnowledgeEngine
from core.few_shot import FewShotRetriever
from core.providers.mock import MockProvider
from core.providers.base import ProviderError
from core.providers.cascade import CascadingProvider
from core.agents.classifier import QueryClassifier
from core.agents.critic import SemanticCritic
from core.agents.drafter import SQLDrafter
from core.pipeline import Pipeline

SAMPLE_SEED_SQL = """
CREATE TABLE departments (id INT PRIMARY KEY, name TEXT);
CREATE TABLE employees (id INT PRIMARY KEY, name TEXT, department TEXT, salary INT);

INSERT INTO departments VALUES (10, 'Sales'), (20, 'Engineering');
INSERT INTO employees VALUES (1, 'Alice Smith', 'Sales', 75000);
INSERT INTO employees VALUES (2, 'Bob Jones', 'Engineering', 95000);
"""


def test_domain_knowledge_value_and_rule_resolution():
    adapter = SQLiteAdapter(seed_sql=SAMPLE_SEED_SQL)
    knowledge = DomainKnowledgeEngine(adapter)

    # Register custom business rule
    knowledge.register_rule(
        keywords=["top earners", "high earners"],
        name="High Earner",
        description="Employees with salary >= 90000",
        sql_expression="salary >= 90000",
    )

    ctx = knowledge.resolve("Show high earners in Sales")
    # Should detect 'Sales' literal
    vals = [v[2] for v in ctx.matched_values]
    assert "Sales" in vals

    # Should detect 'high earners' rule
    rule_names = [r.name for r in ctx.matched_rules]
    assert "High Earner" in rule_names

    prompt_text = ctx.to_prompt_text()
    assert "Sales" in prompt_text
    assert "salary >= 90000" in prompt_text


def test_few_shot_retriever():
    retriever = FewShotRetriever(top_k=2)
    # Query about counts/groups
    examples = retriever.retrieve("Count the employees in each department")
    assert len(examples) == 2
    assert any("Count" in ex.question or "COUNT" in ex.sql for ex in examples)


class FailingProvider:
    def generate_text(self, *args, **kwargs):
        raise ProviderError("Primary provider connection timeout")


def test_cascading_provider():
    failing = FailingProvider()
    backup = MockProvider()
    cascade = CascadingProvider(primary=failing, fallback=backup)

    res = cascade.generate_text("List all employees in Sales")
    assert "SELECT" in res
    assert cascade.last_provider_used == "fallback"


def test_query_classifier():
    classifier = QueryClassifier()
    c1 = classifier.classify("List all employees")
    assert c1.complexity == "EASY"
    assert c1.requires_join is False

    c2 = classifier.classify("What is the average sales amount per department?")
    assert c2.complexity == "MEDIUM"
    assert c2.requires_aggregation is True

    c3 = classifier.classify(
        "Which employees have no sales and earn more than average?"
    )
    assert c3.complexity == "COMPLEX"
    assert c3.requires_subquery is True


def test_semantic_critic_case_mismatch():
    adapter = SQLiteAdapter(seed_sql=SAMPLE_SEED_SQL)
    critic = SemanticCritic()

    # Query with wrong casing: returns 0 rows
    empty_result = adapter.execute_readonly(
        "SELECT * FROM employees WHERE department = 'sales';"
    )
    review = critic.review(
        question="List employees in sales",
        sql="SELECT * FROM employees WHERE department = 'sales';",
        result=empty_result,
    )

    assert review.is_acceptable is False
    assert "case sensitivity" in review.feedback.lower()


def test_full_decomposed_pipeline_end_to_end():
    adapter = SQLiteAdapter(seed_sql=SAMPLE_SEED_SQL)
    knowledge = DomainKnowledgeEngine(adapter)
    provider = MockProvider()
    drafter = SQLDrafter(provider=provider)

    pipeline = Pipeline(
        adapter=adapter,
        generator=drafter,
        knowledge_engine=knowledge,
        classifier=QueryClassifier(),
        critic=SemanticCritic(),
    )

    result = pipeline.ask("List all employees in the Engineering department")
    assert result.error is None
    assert result.result is not None
    assert len(result.result.rows) > 0
    assert "classify" in result.timings
    assert "knowledge" in result.timings
    assert result.classification.complexity in ("EASY", "MEDIUM")
