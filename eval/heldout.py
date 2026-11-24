"""Held-out Text-to-SQL benchmark with leakage checks, ablations and calibration.

The questions in ``heldout_benchmark.json`` must never appear in anything the
pipeline can learn from: the hand-written few-shot examples, the semantic-layer
YAML, or the local feedback store. ``check_leakage`` enforces the first two and
``build_variant`` always builds pipelines with feedback disabled.
"""
import json
import math
import re
from pathlib import Path
from typing import Any, Callable

import sqlglot

from core.few_shot import FewShotRetriever
from core.knowledge import DomainKnowledgeEngine
from core.loader import SEMANTIC_DIR, _get_few_shot_examples, build_pipeline
from core.retriever import SchemaRetriever
from core.semantic_layer import SemanticLayer

from .runner import EvalItemResult, EvalRunner

DATASET_PATH = Path(__file__).with_name("heldout_benchmark.json")

# Each config switches off exactly one component, except "baseline_minimal",
# which switches them all off and is the lower bound.
CONFIGS: dict[str, dict[str, Any]] = {
    "full": {},
    "no_repair": {"max_repairs": 0},
    "no_few_shot": {"few_shot": False},
    "no_semantic_layer": {"semantic_layer": False},
    "no_schema_pruning": {"schema_pruning": False},
    "no_value_linking": {"value_linking": False},
    "no_confidence": {"confidence_samples": 1},
    "baseline_minimal": {
        "max_repairs": 0,
        "few_shot": False,
        "semantic_layer": False,
        "schema_pruning": False,
        "value_linking": False,
        "confidence_samples": 1,
    },
}


def load_dataset(path: str | Path = DATASET_PATH) -> list[dict]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _norm_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _norm_sql(sql: str) -> str:
    try:
        return sqlglot.parse_one(sql, read="sqlite").sql(dialect="sqlite").lower()
    except sqlglot.errors.SqlglotError:
        return _norm_text(sql)


def check_leakage(items: list[dict], near_duplicate_threshold: float = 0.8) -> dict:
    """Return hard leaks (must be empty) and near-duplicate warnings."""
    leaks: list[dict] = []
    warnings: list[dict] = []
    yaml_text: dict[str, str] = {}
    for item in items:
        domain = item["domain"]
        pool = _get_few_shot_examples(domain)
        question = _norm_text(item["question"])
        gold = _norm_sql(item["query"])
        for example in pool:
            if _norm_text(example.question) == question:
                leaks.append({"id": item["id"], "kind": "few_shot_question", "match": example.question})
            if _norm_sql(example.sql) == gold:
                leaks.append({"id": item["id"], "kind": "few_shot_sql", "match": example.sql})
        if domain not in yaml_text:
            path = Path(SEMANTIC_DIR) / f"{domain}.yaml"
            yaml_text[domain] = _norm_text(path.read_text(encoding="utf-8")) if path.exists() else ""
        if question and question in yaml_text[domain]:
            leaks.append({"id": item["id"], "kind": "semantic_yaml", "match": item["question"]})
        if pool:
            # Fit on pool + question so words the pool never uses still count;
            # otherwise a question sharing one word with the pool scores 1.0.
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity

            texts = [example.question for example in pool] + [item["question"]]
            matrix = TfidfVectorizer(stop_words="english").fit_transform(texts)
            similarity = float(cosine_similarity(matrix[-1], matrix[:-1]).max())
            if similarity >= near_duplicate_threshold:
                warnings.append({"id": item["id"], "similarity": round(similarity, 3)})
    return {"leaks": leaks, "near_duplicates": warnings}


def build_variant(domain: str, provider, flags: dict[str, Any]):
    """Build a fresh pipeline for one domain with the requested components disabled."""
    pipeline, adapter, _ = build_pipeline(
        db_engine="sqlite",
        schema_name=domain,
        provider=provider,
        include_feedback=False,
    )
    if "max_repairs" in flags:
        pipeline.max_repairs = flags["max_repairs"]
    if "confidence_samples" in flags:
        pipeline.confidence_samples = flags["confidence_samples"]
    if flags.get("few_shot") is False:
        pipeline.generator.few_shot_retriever = FewShotRetriever(examples=[], top_k=2)
    if flags.get("semantic_layer") is False:
        pipeline.retriever = SchemaRetriever()
        pipeline.knowledge_engine = DomainKnowledgeEngine(adapter, semantic_layer=SemanticLayer())
    if flags.get("schema_pruning") is False:
        pipeline.retriever = None
    if flags.get("value_linking") is False:
        pipeline.knowledge_engine = None
    return pipeline, adapter


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (round(100 * (centre - margin), 1), round(100 * (centre + margin), 1))


def summarize(details: list[EvalItemResult]) -> dict:
    total = len(details)
    correct = sum(d.is_exec_match for d in details)
    valid = sum(d.is_valid_sql for d in details)
    answered = sum(not d.abstained for d in details)
    abstained = [d for d in details if d.abstained]
    pct = lambda k, n: round(100.0 * k / n, 1) if n else 0.0
    low, high = wilson_interval(correct, total)
    return {
        "n": total,
        "ex": pct(correct, total),
        "ex_ci95": [low, high],
        "valid_sql_rate": pct(valid, total),
        "coverage": pct(answered, total),
        "selective_ex": pct(correct, answered),
        "abstained": len(abstained),
        "abstained_would_have_been_wrong": sum(d.would_be_correct is False for d in abstained),
        "abstained_would_have_been_correct": sum(d.would_be_correct is True for d in abstained),
    }


def confidence_table(details: list[EvalItemResult]) -> list[dict]:
    """Accuracy of the query the model produced, bucketed by its confidence score.

    Abstained items use the query they would have returned, so low-confidence
    buckets are not empty by construction.
    """
    buckets: dict[float, list[bool]] = {}
    for detail in details:
        if detail.confidence is None:
            continue
        correct = bool(detail.would_be_correct) if detail.abstained else detail.is_exec_match
        buckets.setdefault(round(detail.confidence, 3), []).append(correct)
    return [
        {
            "confidence": confidence,
            "n": len(values),
            "accuracy": round(100.0 * sum(values) / len(values), 1),
        }
        for confidence, values in sorted(buckets.items())
    ]


def run_config(
    items: list[dict],
    provider,
    flags: dict[str, Any],
    on_item: Callable[[str], None] | None = None,
) -> dict:
    details: list[EvalItemResult] = []
    for domain in sorted({item["domain"] for item in items}):
        domain_items = [item for item in items if item["domain"] == domain]
        pipeline, adapter = build_variant(domain, provider, flags)
        try:
            runner = EvalRunner(pipeline=pipeline, adapter=adapter)
            details.extend(runner.evaluate_dataset(domain_items).details)
        finally:
            adapter.close()
        if on_item:
            on_item(domain)
    by = lambda key: {
        value: summarize([d for d in details if getattr(d, key) == value])
        for value in sorted({getattr(d, key) for d in details if getattr(d, key)})
    }
    return {
        "flags": flags,
        "overall": summarize(details),
        "by_domain": by("domain"),
        "by_difficulty": by("difficulty"),
        "confidence_buckets": confidence_table(details),
        "details": [d.__dict__ for d in details],
    }


def run_heldout(
    provider,
    config_names: list[str] | None = None,
    limit: int | None = None,
    domains: list[str] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    items = load_dataset()
    leakage = check_leakage(items)
    if leakage["leaks"]:
        raise RuntimeError(f"Benchmark leakage detected: {leakage['leaks'][:3]}")
    if domains:
        items = [item for item in items if item["domain"] in domains]
    if limit:
        items = items[:limit]
    names = config_names or list(CONFIGS)
    unknown = [name for name in names if name not in CONFIGS]
    if unknown:
        raise ValueError(f"Unknown config(s): {unknown}. Choose from {list(CONFIGS)}.")
    results = {}
    for name in names:
        if on_progress:
            on_progress(name)
        results[name] = run_config(items, provider, CONFIGS[name])
    return {"n_questions": len(items), "leakage": leakage, "configs": results}


def format_markdown(report: dict) -> str:
    lines = [
        f"# Held-out benchmark ({report['n_questions']} questions)",
        "",
        "EX counts an abstention as a wrong answer. Coverage is the share of questions "
        "answered, and selective EX is accuracy on the answered ones. The 95% interval "
        "is a Wilson interval; with this many questions it is wide, so treat "
        "differences of a few points as noise.",
        "",
        "| Config | EX % | 95% CI | Valid SQL % | Coverage % | Selective EX % |",
        "|---|---|---|---|---|---|",
    ]
    for name, data in report["configs"].items():
        o = data["overall"]
        lines.append(
            f"| {name} | {o['ex']} | {o['ex_ci95'][0]}-{o['ex_ci95'][1]} | "
            f"{o['valid_sql_rate']} | {o['coverage']} | {o['selective_ex']} |"
        )
    full = report["configs"].get("full")
    if full:
        lines += ["", "## Full pipeline by difficulty", "", "| Difficulty | n | EX % |", "|---|---|---|"]
        for name, data in full["by_difficulty"].items():
            lines.append(f"| {name} | {data['n']} | {data['ex']} |")
        lines += ["", "## Full pipeline by schema", "", "| Schema | n | EX % |", "|---|---|---|"]
        for name, data in full["by_domain"].items():
            lines.append(f"| {name} | {data['n']} | {data['ex']} |")
        lines += [
            "", "## Confidence vs accuracy (full pipeline)", "",
            "Accuracy of the query the model produced, including queries it abstained on.",
            "", "| Confidence | n | Accuracy % |", "|---|---|---|",
        ]
        for bucket in full["confidence_buckets"]:
            lines.append(f"| {bucket['confidence']} | {bucket['n']} | {bucket['accuracy']} |")
        o = full["overall"]
        lines += [
            "",
            f"Abstained on {o['abstained']} questions: "
            f"{o['abstained_would_have_been_wrong']} would have been wrong, "
            f"{o['abstained_would_have_been_correct']} would have been correct.",
        ]
    return "\n".join(lines) + "\n"
