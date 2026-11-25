# NLPSQL project summary

**Prepared:** 25 November 2026  
**License:** MIT (`RitamRoa` is the copyright holder named in `LICENSE`; confirm ownership before redistribution).

## Problem and approach

NLPSQL turns natural-language questions into read-only SQL over a selected relational schema. The challenge is not only generating syntactically valid SQL: the system must ground the query in the active schema, execute it safely, apply role-based access controls, and avoid returning an answer when candidate queries disagree.

The pipeline translates supported non-English input offline by default, retrieves and prunes schema context, resolves relevant domain terms and values, classifies the request, drafts SQL with few-shot examples, validates the SQL, executes it read-only, and can retry a failed or semantically weak query. It compares up to three candidate results; if their agreement is below the configured threshold, it abstains. Role policies additionally restrict schemas, tables and columns, apply row filters, and mask sensitive results. Audit events record hashes rather than raw questions and SQL.

## RBAC regression fixed

A CTE could reuse the name of a real table, such as `WITH employees AS (SELECT * FROM employees) ...`. The policy layer treated the CTE name as a separate source, allowing the query to reach the underlying table without its required row filter and masking rules. The validator/policy boundary now rejects CTEs that shadow real tables. Regression cases cover visible and hidden tables, case variation, and nested CTEs on SQLite and DuckDB. The security regression suite is run with:

```powershell
py -m pytest -q tests/test_rbac_regressions.py
```

## Benchmark method and current results

The held-out set contains 80 questions across four bundled schemas and three difficulty levels. The harness blocks exact question/gold-SQL leakage against few-shot examples and semantic-layer files, warns on near-duplicates, and disables saved feedback in benchmark pipelines. Ablations compare the full pipeline with settings that disable repair, few-shot examples, semantic grounding, schema pruning, value linking, or confidence sampling, plus a minimal baseline. Reported measures include execution accuracy with a 95% Wilson interval, valid-SQL rate, coverage, selective accuracy, confidence buckets, and per-schema/difficulty details.

**No model-backed benchmark results are available yet.** The checked environment selected `MockProvider`, and the required GGUF file was absent. The benchmark correctly refused both the smoke and full commands; no accuracy values are reported here. Run the scripts with a real provider and replace this section only with the resulting dated metrics. Keep in mind that these are tiny demonstration databases and 80 questions produce wide uncertainty intervals; execution match on them is not evidence of performance on Spider or BIRD.

## Limits

The small model (when configured) may need schema guidance and examples, and confidence is a candidate-result agreement heuristic rather than a calibrated probability. The sample databases are tiny. The threaded HTTP server serializes generation through its shared pipeline and is intended for trusted local use; use proper authentication, TLS, least-privilege database credentials, and deployment infrastructure before exposing it to a network. Feedback is local, role-and-schema scoped, and contains user questions and corrected SQL.
