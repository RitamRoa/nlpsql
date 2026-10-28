"""
Benchmark & Evaluation Script for NLPSQL.
Runs Spider/BIRD style Execution Accuracy (EX), Valid SQL Rate, and Ablation Study.

Usage:
    python run_benchmark.py
"""

import json
import time
from core.loader import build_pipeline
from core.generator import MockGenerator, SQLGenerator
from eval.runner import EvalRunner, run_ablation_study
import os

BENCHMARK_DATASET = [
    {
        "domain": "company",
        "question": "List all employees in Engineering",
        "query": "SELECT e.name, e.department, e.salary, e.hire_date, e.job_title, e.email, e.performance_rating FROM employees e WHERE e.department = 'Engineering';",
    },
    {
        "domain": "company",
        "question": "What is the average salary by department?",
        "query": "SELECT department, AVG(salary) as avg_sal FROM employees GROUP BY department;",
    },
    {
        "domain": "company",
        "question": "Show all remote employees",
        "query": "SELECT * FROM employees WHERE is_remote = 1;",
    },
    {
        "domain": "company",
        "question": "Which projects are currently active?",
        "query": "SELECT name, budget, status FROM projects WHERE status = 'Active';",
    },
    {
        "domain": "company",
        "question": "Find employees with salary greater than 70000",
        "query": "SELECT * FROM employees WHERE salary > 70000;",
    },
]


def run_benchmark():
    print("=" * 60)
    print("           NLPSQL BENCHMARK & EVALUATION SUITE            ")
    print("=" * 60)

    model_path = os.environ.get("MODEL_PATH", "SmolLM2-135M-text-to-sql.Q8_0.gguf")
    if os.path.exists(model_path) and os.environ.get("MOCK_LLM", "0") != "1":
        print(f"[*] Evaluator Engine: Local GGUF Model ({model_path})")
        provider = SQLGenerator(model_path=model_path)
    else:
        print("[*] Evaluator Engine: MockProvider (offline test mode)")
        provider = MockGenerator()

    # Build the full multi-agent pipeline
    pipeline, adapter, _ = build_pipeline(
        db_engine="sqlite",
        schema_name="company",
        provider=provider,
    )

    runner = EvalRunner(pipeline=pipeline, adapter=adapter)
    print(f"[*] Running evaluation on {len(BENCHMARK_DATASET)} test queries...")

    report = runner.evaluate_dataset(BENCHMARK_DATASET)

    # Print summary table
    print("\n" + report.summary())

    print("\nDetailed Per-Query Results:")
    print("-" * 60)
    for idx, d in enumerate(report.details, 1):
        status = "MATCH" if d.is_exec_match else ("VALID (diff)" if d.is_valid_sql else "ERROR")
        print(f"{idx}. [{status}] Q: {d.question}")
        print(f"   Pred SQL: {d.pred_sql}")
        if d.error:
            print(f"   Error:    {d.error}")
        print(f"   Attempts: {d.attempts}")
        print()

    # Run Ablation Study (Single-shot vs Multi-Agent Repair)
    print("=" * 60)
    print("              RUNNING ABLATION STUDY                      ")
    print("=" * 60)
    ablation = run_ablation_study(
        dataset=BENCHMARK_DATASET,
        adapter=adapter,
        generator=pipeline.generator,
        retriever=pipeline.retriever,
        knowledge_engine=pipeline.knowledge_engine,
        classifier=pipeline.classifier,
        critic=pipeline.critic,
    )

    base_m = ablation["baseline_single_shot"].metrics
    rep_m = ablation["with_self_repair"].metrics

    print(f"| Metric                      | Baseline (No Repair) | Multi-Agent (Self-Repair) |")
    print(f"|-----------------------------|----------------------|---------------------------|")
    print(f"| Valid SQL Rate (%)          | {base_m.valid_sql_rate:20.1f}% | {rep_m.valid_sql_rate:25.1f}% |")
    print(f"| Execution Accuracy (EX %)   | {base_m.exec_accuracy:20.1f}% | {rep_m.exec_accuracy:25.1f}% |")
    print(f"| Avg Total Latency (s)       | {base_m.avg_total_time_s:20.3f}s | {rep_m.avg_total_time_s:25.3f}s |")
    print("=" * 60)


if __name__ == "__main__":
    run_benchmark()
