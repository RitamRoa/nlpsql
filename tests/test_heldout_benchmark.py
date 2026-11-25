"""Tests for the held-out benchmark: data quality, leakage guard and scoring logic.

The oracle and flaky providers below stand in for a real model so the harness can
be verified without model weights: an oracle must score 100%, and a provider that
disagrees with itself must be made to abstain.
"""
import pytest
import sqlglot

from core.adapters.base import ExecutionError
from core.feedback import FeedbackStore
from core.few_shot import FewShotRetriever
from core.loader import build_pipeline, create_adapter
from core.validator import validate
from eval.heldout import (
    CONFIGS,
    build_variant,
    check_leakage,
    confidence_table,
    format_markdown,
    load_dataset,
    run_config,
    run_heldout,
    summarize,
    wilson_interval,
)
from eval.metrics import compare_results

ITEMS = load_dataset()
BY_DOMAIN = {}
for _item in ITEMS:
    BY_DOMAIN.setdefault(_item["domain"], []).append(_item)


class OracleProvider:
    """Returns gold SQL for whichever benchmark question appears in the prompt."""

    def __init__(self, items):
        self.items = sorted(items, key=lambda item: -len(item["question"]))

    def find(self, prompt):
        for item in self.items:
            if item["question"] in prompt:
                return item
        raise AssertionError("prompt did not contain a benchmark question")

    def generate_text(self, prompt, stop=None, max_tokens=150, temperature=0.0):
        return self.find(prompt)["query"]


class FlakyProvider(OracleProvider):
    """Right at temperature 0, but each sampled retry gives a different wrong query."""

    def __init__(self, items):
        super().__init__(items)
        self.calls = {}

    def generate_text(self, prompt, stop=None, max_tokens=150, temperature=0.0):
        item = self.find(prompt)
        if temperature == 0.0:
            return item["query"]
        siblings = [other for other in self.items if other["domain"] == item["domain"]]
        siblings.sort(key=lambda other: other["id"])
        count = self.calls[item["id"]] = self.calls.get(item["id"], 0) + 1
        index = siblings.index(item)
        return siblings[(index + count) % len(siblings)]["query"]


# --------------------------------------------------------------------------- data


def test_dataset_shape():
    assert len(ITEMS) == 80
    assert len({item["id"] for item in ITEMS}) == 80
    assert {domain: len(rows) for domain, rows in BY_DOMAIN.items()} == {
        "company": 20, "ecommerce": 20, "university": 20, "healthcare": 20,
    }
    for rows in BY_DOMAIN.values():
        counts = {level: sum(r["difficulty"] == level for r in rows) for level in ("easy", "medium", "hard")}
        assert all(counts.values()), counts


@pytest.mark.parametrize("item", ITEMS, ids=[item["id"] for item in ITEMS])
def test_gold_query_is_valid_and_returns_rows(item):
    adapter = create_adapter("sqlite", schema_name=item["domain"])
    try:
        validate(item["query"], adapter.get_schema(), "sqlite")
        result = adapter.execute_readonly(item["query"], limit=500)
    finally:
        adapter.close()
    # An empty gold result would let a model that returns nothing score as correct.
    assert result.rows, "gold query returns no rows"
    assert result.rows != [(None,)], "gold query returns only NULL"


@pytest.mark.parametrize(
    "item",
    [item for item in ITEMS if "LIMIT" in item["query"].upper()],
    ids=lambda item: item["id"],
)
def test_limit_queries_have_no_tie_at_the_cutoff(item):
    """A tie at the LIMIT boundary makes the gold answer depend on row order."""
    tree = sqlglot.parse_one(item["query"], read="sqlite")
    cutoff = int(tree.args["limit"].expression.this)
    key = tree.args["order"].expressions[0].this.copy()
    tree.set("limit", None)
    tree.select(key.as_("tie_key"), append=True, copy=False)
    adapter = create_adapter("sqlite", schema_name=item["domain"])
    try:
        rows = adapter.execute_readonly(tree.sql(dialect="sqlite"), limit=500).rows
    finally:
        adapter.close()
    assert len(rows) > cutoff
    assert rows[cutoff - 1][-1] != rows[cutoff][-1], "tie at the LIMIT cutoff"


# ------------------------------------------------------------------------ leakage


def test_benchmark_has_no_leakage():
    report = check_leakage(ITEMS)
    assert report["leaks"] == []


def test_leakage_check_detects_a_copied_question_and_copied_sql():
    from core.loader import _get_few_shot_examples

    example = _get_few_shot_examples("company")[1]
    copied_question = {"id": "x1", "domain": "company", "difficulty": "easy",
                       "question": example.question.upper() + "?", "query": "SELECT 1;"}
    copied_sql = {"id": "x2", "domain": "company", "difficulty": "easy",
                  "question": "A brand new phrasing", "query": example.sql}
    kinds = {leak["kind"] for leak in check_leakage([copied_question, copied_sql])["leaks"]}
    assert kinds == {"few_shot_question", "few_shot_sql"}


def test_run_heldout_refuses_to_run_when_leaking(monkeypatch):
    monkeypatch.setattr(
        "eval.heldout.check_leakage",
        lambda items: {"leaks": [{"id": "x", "kind": "few_shot_question", "match": "q"}], "near_duplicates": []},
    )
    with pytest.raises(RuntimeError, match="leakage"):
        run_heldout(OracleProvider(ITEMS), ["full"], limit=1)


# ------------------------------------------------------------- bugs fixed alongside


def test_empty_few_shot_list_means_no_examples():
    assert FewShotRetriever(examples=[]).retrieve("List all employees in the Sales department") == []
    assert FewShotRetriever().examples, "None must still mean the built-in defaults"


def test_feedback_is_excluded_from_benchmark_pipelines(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    FeedbackStore().add("company", "my private corrected question", "SELECT 1;", rating=1, corrected=True)

    included, adapter_a, _ = build_pipeline(schema_name="company", include_feedback=True)
    excluded, adapter_b, _ = build_pipeline(schema_name="company", include_feedback=False)
    questions = lambda pipeline: [e.question for e in pipeline.generator.few_shot_retriever.examples]
    assert "my private corrected question" in questions(included)
    assert "my private corrected question" not in questions(excluded)
    adapter_a.close()
    adapter_b.close()


# ------------------------------------------------------------------------ configs


def test_config_flags_take_effect():
    provider = OracleProvider(ITEMS)
    pipeline, adapter = build_variant("company", provider, CONFIGS["full"])
    assert pipeline.max_repairs == 1 and pipeline.confidence_samples == 3
    assert pipeline.retriever and pipeline.knowledge_engine
    assert pipeline.generator.few_shot_retriever.examples
    adapter.close()

    pipeline, adapter = build_variant("company", provider, CONFIGS["baseline_minimal"])
    assert pipeline.max_repairs == 0 and pipeline.confidence_samples == 1
    assert pipeline.retriever is None and pipeline.knowledge_engine is None
    assert pipeline.generator.few_shot_retriever.examples == []
    adapter.close()

    pipeline, adapter = build_variant("company", provider, CONFIGS["strict_abstain"])
    assert pipeline.confidence_threshold == 1.0 and pipeline.confidence_samples == 3
    adapter.close()

    pipeline, adapter = build_variant("company", provider, CONFIGS["no_semantic_layer"])
    assert pipeline.retriever.semantic_layer.tables == {}
    assert pipeline.knowledge_engine is not None
    adapter.close()


# ------------------------------------------------------------------------ scoring


def test_oracle_scores_perfectly_when_nothing_is_ablated():
    report = run_config(ITEMS, OracleProvider(ITEMS), CONFIGS["no_schema_pruning"])
    overall = report["overall"]
    assert overall["n"] == 80
    assert overall["valid_sql_rate"] == 100.0
    assert overall["ex"] == 100.0
    assert overall["coverage"] == 100.0
    assert report["confidence_buckets"] == [{"confidence": 1.0, "n": 80, "accuracy": 100.0}] or \
        report["confidence_buckets"] == []  # no_confidence-style configs report none


def test_oracle_under_the_full_pipeline():
    """Schema pruning is part of 'full'; an oracle shows how much it costs."""
    report = run_config(ITEMS, OracleProvider(ITEMS), CONFIGS["full"])
    overall = report["overall"]
    assert overall["ex"] >= 90.0, f"oracle only reaches {overall['ex']}%: {[d['id'] for d in report['details'] if not d['is_exec_match']]}"
    assert report["confidence_buckets"] == [{"confidence": 1.0, "n": 80, "accuracy": overall["ex"]}] or \
        all(b["confidence"] == 1.0 for b in report["confidence_buckets"])


def test_substituting_wrong_queries_lowers_the_score_by_exactly_the_mismatches():
    class SwapProvider(OracleProvider):
        def generate_text(self, prompt, stop=None, max_tokens=150, temperature=0.0):
            item = self.find(prompt)
            siblings = sorted((o for o in self.items if o["domain"] == item["domain"]), key=lambda o: o["id"])
            return siblings[(siblings.index(item) + 1) % len(siblings)]["query"]

    expected_correct = 0
    for domain, rows in BY_DOMAIN.items():
        adapter = create_adapter("sqlite", schema_name=domain)
        rows = sorted(rows, key=lambda r: r["id"])
        for index, row in enumerate(rows):
            swapped = rows[(index + 1) % len(rows)]
            expected_correct += compare_results(
                adapter.execute_readonly(swapped["query"]), adapter.execute_readonly(row["query"])
            )
        adapter.close()

    report = run_config(ITEMS, SwapProvider(ITEMS), CONFIGS["baseline_minimal"])
    assert report["overall"]["ex"] == round(100.0 * expected_correct / 80, 1)
    assert report["overall"]["ex"] < 20.0


def test_disagreeing_candidates_cause_abstention_and_are_scored_as_such():
    report = run_config(ITEMS, FlakyProvider(ITEMS), CONFIGS["full"])
    overall = report["overall"]
    assert overall["abstained"] > 60, "sampled candidates all differ, so most answers should be withheld"
    assert overall["coverage"] < 25.0
    assert overall["ex"] <= overall["coverage"], "abstentions must count as wrong, never as right"
    assert overall["abstained_would_have_been_correct"] > 0
    low = [b for b in report["confidence_buckets"] if b["confidence"] < 0.5]
    assert low and low[0]["accuracy"] > 80.0, "the primary query was correct, and the table must show that"


def test_confidence_table_ignores_items_without_a_confidence_score():
    from eval.runner import EvalItemResult

    base = dict(question="q", gold_sql="g", pred_sql="p", is_valid_sql=True, attempts=1, error=None)
    rows = [
        EvalItemResult(is_exec_match=True, confidence=1.0, **base),
        EvalItemResult(is_exec_match=False, confidence=1.0, **base),
        EvalItemResult(is_exec_match=False, confidence=None, **base),
        EvalItemResult(is_exec_match=False, abstained=True, would_be_correct=True, confidence=0.333, **base),
    ]
    assert confidence_table(rows) == [
        {"confidence": 0.333, "n": 1, "accuracy": 100.0},
        {"confidence": 1.0, "n": 2, "accuracy": 50.0},
    ]
    assert summarize(rows)["abstained_would_have_been_correct"] == 1


def test_wilson_interval_behaves():
    assert wilson_interval(0, 0) == (0.0, 0.0)
    low, high = wilson_interval(80, 80)
    assert high == 100.0 and 90.0 < low < 100.0
    low, high = wilson_interval(40, 80)
    assert low < 50.0 < high


def test_markdown_report_contains_every_section():
    report = run_heldout(OracleProvider(ITEMS), ["full", "baseline_minimal"], limit=20)
    text = format_markdown(report)
    for heading in ("# Held-out benchmark (20 questions)", "| full |", "| baseline_minimal |",
                    "## Full pipeline by difficulty", "## Confidence vs accuracy"):
        assert heading in text


def test_unknown_config_is_rejected():
    with pytest.raises(ValueError, match="Unknown config"):
        run_heldout(OracleProvider(ITEMS), ["not_a_config"])
