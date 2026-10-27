import json
import os
import time
from dataclasses import dataclass, field
from typing import Any
from core.adapters.base import DBAdapter, Result, ExecutionError
from core.pipeline import Pipeline
from .metrics import EvalMetrics, compare_results


@dataclass
class EvalItemResult:
    question: str
    gold_sql: str
    pred_sql: str | None
    is_valid_sql: bool
    is_exec_match: bool
    attempts: int
    error: str | None
    timings: dict = field(default_factory=dict)


@dataclass
class BenchmarkReport:
    metrics: EvalMetrics
    details: list[EvalItemResult]

    def summary(self) -> str:
        m = self.metrics
        return (
            f"=== Text-to-SQL Benchmark Report ===\n"
            f"Total Queries Evaluated: {m.total_examples}\n"
            f"Valid SQL Rate (V):      {m.valid_sql_rate:.1f}%\n"
            f"Execution Accuracy (EX): {m.exec_accuracy:.1f}%\n"
            f"Repaired Queries:        {m.repaired_success_count}\n"
            f"Avg Generation Latency:  {m.avg_inference_time_s:.3f}s\n"
            f"Avg Total Latency:       {m.avg_total_time_s:.3f}s\n"
            f"====================================="
        )


class EvalRunner:
    """Evaluation runner for Spider / BIRD benchmark datasets."""

    def __init__(self, pipeline: Pipeline, adapter: DBAdapter):
        self.pipeline = pipeline
        self.adapter = adapter

    def evaluate_dataset(
        self,
        dataset: list[dict[str, Any]] | str,
        limit: int | None = None,
    ) -> BenchmarkReport:
        """Run evaluation on a dataset.

        dataset: either a list of dicts (with 'question' and 'query' keys)
                 or a path to a JSON file.
        """
        if isinstance(dataset, str):
            with open(dataset, "r", encoding="utf-8") as f:
                items = json.load(f)
        else:
            items = dataset

        if limit:
            items = items[:limit]

        details: list[EvalItemResult] = []
        valid_count = 0
        exec_match_count = 0
        repaired_count = 0
        total_gen_time = 0.0
        total_pipeline_time = 0.0

        for item in items:
            question = item["question"]
            gold_sql = item["query"]

            t0 = time.perf_counter()
            pipe_res = self.pipeline.ask(question)
            t_total = time.perf_counter() - t0
            total_pipeline_time += t_total

            gen_time = sum(
                v for k, v in pipe_res.timings.items() if k.startswith("generate")
            )
            total_gen_time += gen_time

            is_valid = pipe_res.error is None and pipe_res.result is not None
            is_match = False

            # Run gold SQL for ground truth execution output
            gold_result: Result | None = None
            try:
                gold_result = self.adapter.execute_readonly(gold_sql)
            except ExecutionError:
                gold_result = None

            if is_valid and gold_result is not None:
                is_match = compare_results(pipe_res.result, gold_result)

            if is_valid:
                valid_count += 1
                if pipe_res.attempts > 1:
                    repaired_count += 1

            if is_match:
                exec_match_count += 1

            details.append(
                EvalItemResult(
                    question=question,
                    gold_sql=gold_sql,
                    pred_sql=pipe_res.sql,
                    is_valid_sql=is_valid,
                    is_exec_match=is_match,
                    attempts=pipe_res.attempts,
                    error=pipe_res.error,
                    timings=pipe_res.timings,
                )
            )

        n = len(items)
        metrics = EvalMetrics(
            total_examples=n,
            valid_sql_count=valid_count,
            exec_accuracy_count=exec_match_count,
            repaired_success_count=repaired_count,
            avg_inference_time_s=round(total_gen_time / n, 4) if n > 0 else 0.0,
            avg_total_time_s=round(total_pipeline_time / n, 4) if n > 0 else 0.0,
        )

        return BenchmarkReport(metrics=metrics, details=details)


def run_ablation_study(
    dataset: list[dict[str, Any]],
    adapter: DBAdapter,
    generator: Any,
    retriever: Any | None = None,
) -> dict[str, BenchmarkReport]:
    """Runs an ablation comparing baseline (no repair) vs pipeline (with 1-shot self-repair)."""
    # Baseline: max_repairs = 0
    p_baseline = Pipeline(
        adapter=adapter, generator=generator, retriever=retriever, max_repairs=0
    )
    runner_baseline = EvalRunner(pipeline=p_baseline, adapter=adapter)
    res_baseline = runner_baseline.evaluate_dataset(dataset)

    # With Self-Repair: max_repairs = 1
    p_repair = Pipeline(
        adapter=adapter, generator=generator, retriever=retriever, max_repairs=1
    )
    runner_repair = EvalRunner(pipeline=p_repair, adapter=adapter)
    res_repair = runner_repair.evaluate_dataset(dataset)

    return {
        "baseline_single_shot": res_baseline,
        "with_self_repair": res_repair,
    }
