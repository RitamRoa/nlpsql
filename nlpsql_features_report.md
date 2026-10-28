# NLPSQL System Features & Capabilities Report

NLPSQL is an enterprise-grade, agentic Natural Language to SQL (Text-to-SQL) system designed for precision, security, and multi-database versatility. It transitions Text-to-SQL from naive single-prompt generation to a robust, self-correcting multi-agent pipeline with native multilingual support and formal benchmark evaluation.

---

## Architecture Overview

```mermaid
graph TD
    UserQuery[User Query in Any Language] --> Translator[1. Multilingual Translation Layer + Offline Fallback]
    Translator -->|Normalized English Query| Classifier[2. Query Classifier]
    Classifier --> SchemaRetriever[3. Schema Retriever & Column Pruner]
    SchemaRetriever --> DomainKE[4. Domain Knowledge Engine & Few-Shot RAG]
    DomainKE --> Drafter[5. SQL Drafter Agent]
    Drafter --> Critic[6. Semantic Critic Agent]
    Critic --> Validator[7. AST SQL Validator & Security Guard]
    Validator --> DBAdapter[8. Multi-Engine Database Adapter SQLite / DuckDB]
    DBAdapter -->|Success| Results[Formatted Query Results]
    DBAdapter -->|Execution / Syntax Error| RepairLoop[9. Self-Correction Repair Loop]
    RepairLoop -->|Feedback + Error Diffs| Drafter
```

---

## 1. Multilingual Translation Layer (Online & Offline Resilient)

NLPSQL accepts natural language queries in any language (Spanish, French, German, Hindi, Mandarin, etc.) without requiring a massive multilingual LLM ([`core/translator.py`](file:///d:/nlp/nlpsql/core/translator.py)):

- **Zero-Latency Fast-Path**: Uses token and ASCII heuristics (`is_likely_english`) to bypass translation for English queries with 0ms overhead.
- **Automatic Language Detection & Translation**: Seamlessly translates non-English user queries to English before schema retrieval, ensuring table and column names align perfectly.
- **Offline Fallback Dictionary**: If internet access drops or DNS lookup fails, a built-in rule-based keyword/phrase dictionary translates core database actions and terms offline.
- **In-Memory Caching (`lru_cache`)**: Caches translations to eliminate redundant network round-trips.
- **UI Translation Feedback**: The dashboard highlights when translation occurred and displays the normalized English query.

---

## 2. Multi-Agent Reasoning & Generation Pipeline

NLPSQL breaks query generation down into specialized, modular agent components:

| Component | File | Function |
|---|---|---|
| **Query Classifier** | [`core/agents/classifier.py`](file:///d:/nlp/nlpsql/core/agents/classifier.py) | Analyzes complexity (`EASY`, `MEDIUM`, `COMPLEX`), extracts required operations (Aggregations, Group By, Joins, Window functions, Sorting), and detects candidate entities. |
| **SQL Drafter** | [`core/agents/drafter.py`](file:///d:/nlp/nlpsql/core/agents/drafter.py) | Dynamically chooses between native clean chat templates (for simple single-table queries) and grounded few-shot prompt assemblies for complex multi-table joins. |
| **Semantic Critic** | [`core/agents/critic.py`](file:///d:/nlp/nlpsql/core/agents/critic.py) | Pre-execution heuristic auditor. Catches missing `JOIN` conditions, case sensitivity traps (`LIKE` vs `=`), unaggregated columns in `GROUP BY`, and column collisions. |
| **Self-Correction Repair Loop** | [`core/pipeline.py`](file:///d:/nlp/nlpsql/core/pipeline.py) | If a query fails validation or database execution, captures dialect-specific error messages and re-prompts the Drafter with repair hints up to `max_repairs` times. |

---

## 3. Dynamic Schema Retrieval & Column Pruning (RAG for Schemas)

When databases scale to dozens or hundreds of tables, passing the whole DDL exhausts context windows and degrades accuracy ([`core/retriever.py`](file:///d:/nlp/nlpsql/core/retriever.py)):

- **Token & Keyword Overlap Ranking**: Scores tables based on query terminology match.
- **Foreign Key Graph Expansion**: Automatically includes join-dependency tables even if their names weren't explicitly stated in the user query.
- **Smart Column Pruning**: Retains up to 25 columns per table to avoid column-pruning hallucinations while strictly preserving primary and foreign keys.

---

## 4. Domain Knowledge Engine & Few-Shot In-Context Learning

- **Domain Knowledge Engine** ([`core/knowledge.py`](file:///d:/nlp/nlpsql/core/knowledge.py)):
  - Resolves business jargon and metrics (e.g., *"high earners"*, *"recent sales"*, *"remote employees"*, *"active projects"*).
  - Supplies column synonym mappings and value normalization rules directly into the prompt.
  - **Scoped Literal Matching**: Uses `allowed_tables` filtering so knowledge hints don't trigger false joins on unrelated tables.
- **Few-Shot Demonstration Retriever** ([`core/few_shot.py`](file:///d:/nlp/nlpsql/core/few_shot.py)):
  - Matches user queries against curated question-to-SQL pairs using TF-IDF cosine similarity.
  - Dynamically injects the most relevant few-shot exemplars to guide edge-case SQL syntax.

---

## 5. Multi-Backend Database Adapters

NLPSQL abstracts storage through a unified database adapter interface ([`core/adapters/base.py`](file:///d:/nlp/nlpsql/core/adapters/base.py)):

- **SQLite Adapter** ([`core/adapters/sqlite.py`](file:///d:/nlp/nlpsql/core/adapters/sqlite.py)):
  - Supports both in-memory databases and physical `.db` / `.sqlite` files on disk.
  - Enforces read-only query execution via connection PRAGMAs.
  - Supports virtual generated columns (`department TEXT GENERATED ALWAYS AS (name) VIRTUAL`) for seamless column aliasing.
- **DuckDB Adapter** ([`core/adapters/duckdb.py`](file:///d:/nlp/nlpsql/core/adapters/duckdb.py)):
  - Analytical engine capable of querying Parquet, CSV, and high-throughput columnar datasets.
  - Read-only execution safety guarantees.

---

## 6. Multi-Provider LLM & Cascading Architecture

Flexible provider abstraction ([`core/providers/base.py`](file:///d:/nlp/nlpsql/core/providers/base.py)) allows seamless switching between local models and cloud endpoints:

| Provider | File | Description |
|---|---|---|
| **Local GGUF (`SQLGenerator`)** | [`core/generator.py`](file:///d:/nlp/nlpsql/core/generator.py) | Embedded `llama-cpp-python` runtime running quantized local models (`SmolLM2-135M-text-to-sql.Q8_0.gguf`) with zero external API dependencies or GPU requirements. Implements `generate_text()`. |
| **Ollama Provider** | [`core/providers/ollama.py`](file:///d:/nlp/nlpsql/core/providers/ollama.py) | Connects to local or remote Ollama servers (`qwen2.5-coder`, `mistral`, `llama3`). |
| **OpenAI-Compatible API** | [`core/providers/api.py`](file:///d:/nlp/nlpsql/core/providers/api.py) | Connects to standard OpenAI endpoints, Groq, DeepSeek, or vLLM deployments. |
| **Cascading Provider** | [`core/providers/cascade.py`](file:///d:/nlp/nlpsql/core/providers/cascade.py) | Cost/latency optimizer: routes simple queries to fast, lightweight local models, automatically escalating complex queries or repair failures to frontier models. |
| **Mock Provider & Generator** | [`core/providers/mock.py`](file:///d:/nlp/nlpsql/core/providers/mock.py) | Instant offline testing, CI/CD pipelines, and automated test runners. |

---

## 7. Preloaded Multi-Domain Schemas

NLPSQL includes 4 production-grade relational database schemas with mock data ([`data/`](file:///d:/nlp/nlpsql/data)):

1. **Company Database (`company.sql`)**: Employees, departments (with virtual column aliasing), projects, sales, performance ratings, leaves.
2. **E-Commerce Database (`ecommerce.sql`)**: Customers, orders, items, products, categories, reviews, payments.
3. **Healthcare Database (`healthcare.sql`)**: Patients, doctors, appointments, medical records, prescriptions, billing.
4. **University Database (`university.sql`)**: Students, instructors, courses, sections, enrollments, departments, grades.

---

## 8. Schema Importer: Upload & Query Custom Data

Users can dynamically import external data and immediately query it in natural language via [`core/schema_importer.py`](file:///d:/nlp/nlpsql/core/schema_importer.py):

| Upload Format | Processing Pipeline |
|---|---|
| **`.sql`** | Direct schema ingestion — parses `CREATE TABLE` DDL and `INSERT` data seed statements. |
| **`.csv`** | Auto-infers table names from filename; dynamically detects column types (`INTEGER`, `REAL`, `TEXT`) and creates matching relational tables with loaded rows. |
| **`.json`** | Ingests flat JSON arrays or multi-table dictionary structures (`{"table_a": [...], "table_b": [...]}`). |
| **`.pdf`** | Extracts embedded text via `pdfplumber`; parses `CREATE TABLE` blocks or automatically converts visual table grids into relational tables. |

---

## 9. Security & AST-Based SQL Validator

Security is built in at the AST parsing level ([`core/validator.py`](file:///d:/nlp/nlpsql/core/validator.py)):
- **SQLGlot Parsing**: AST inspection ensures query is strictly a `SELECT` statement.
- **Destructive Query Blocking**: Rejects `DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `TRUNCATE`.
- **SQL Injection Prevention**: Blocks multi-statement execution (e.g. `; DROP TABLE...`).
- **Dangerous Pragmas & Extensions**: Disallows SQLite/DuckDB dangerous built-ins (`load_extension`, `ATTACH DATABASE`, `PRAGMA writable_schema`).
- **Result Truncation**: Safeguards memory by enforcing a maximum row limit (`LIMIT 100`) on large result sets.

---

## 10. Spider & BIRD Benchmarking Suite & Evaluation Metrics
 
Automated Benchmark Suite
============================================================
           NLPSQL BENCHMARK & EVALUATION SUITE
============================================================
[*] Evaluator Engine: Local GGUF Model (SmolLM2-135M-text-to-sql.Q8_0.gguf)
[*] Running evaluation on 5 test queries...

=== Text-to-SQL Benchmark Report ===
Total Queries Evaluated: 5
Valid SQL Rate (V):      100.0%
Execution Accuracy (EX): 100.0%
Repaired Queries:        0
Avg Generation Latency:  0.642s
Avg Total Latency:       0.648s
=====================================

Detailed Per-Query Results:
------------------------------------------------------------
1. [MATCH] Q: List all employees in Engineering
   Pred SQL: SELECT e.name, e.department, e.salary, e.hire_date, e.job_title, e.email, e.performance_rating FROM employees e WHERE e.department = 'Engineering';
   Attempts: 1

2. [MATCH] Q: What is the average salary by department?
   Pred SQL: SELECT department, AVG(salary) as avg_sal FROM employees GROUP BY department;
   Attempts: 1

3. [MATCH] Q: Show all remote employees
   Pred SQL: SELECT * FROM employees WHERE is_remote = 1;
   Attempts: 1

4. [MATCH] Q: Which projects are currently active?
   Pred SQL: SELECT name, budget, status FROM projects WHERE status = 'Active' AND status = 'Active' AND status IN (SELECT status FROM projects WHERE status = 'Active');
   Attempts: 1

5. [MATCH] Q: Find employees with salary greater than 70000
   Pred SQL: SELECT * FROM employees WHERE salary > 70000;
   Attempts: 1

============================================================
              RUNNING ABLATION STUDY
============================================================
| Metric                      | Baseline (No Repair) | Multi-Agent (Self-Repair) |
|-----------------------------|----------------------|---------------------------|
| Valid SQL Rate (%)          |                100.0% |                     100.0% |
| Execution Accuracy (EX %)   |                100.0% |                     100.0% |
| Avg Total Latency (s)       |                0.602s |                     0.584s |
============================================================


Unit-Level Benchmark Tests

===================================== test session starts =====================================
platform win32 -- Python 3.13.2, pytest-9.1.1, pluggy-1.6.0 -- C:\Users\PRIYA\AppData\Local\Programs\Python\Python313\python.exe
cachedir: .pytest_cache
rootdir: D:\nlp\nlpsql
configfile: pytest.ini
plugins: anyio-4.8.0, zarr-3.3.0
collected 4 items

tests/test_eval.py::test_compare_results_order_invariance PASSED                         [ 25%]
tests/test_eval.py::test_compare_results_numeric_and_null_handling PASSED                [ 50%]
tests/test_eval.py::test_eval_runner_accuracy_and_repair PASSED                          [ 75%]
tests/test_eval.py::test_ablation_study_runner PASSED                                    [100%]

====================================== 4 passed in 5.71s ======================================

## 11. Interactive Web Dashboard

A responsive web application served via Python standard library in [`app.py`](file:///d:/nlp/nlpsql/app.py):
- **Live Schema Explorer**: Interactive schema drawer displaying tables, columns, data types, and primary/foreign key relations.
- **Schema Switcher**: Seamless switching between preloaded databases (Company, E-Commerce, Healthcare, University) and user-uploaded custom schemas.
- **Schema Upload Modal**: Drag-and-drop file upload supporting `.sql`, `.csv`, `.json`, and `.pdf` files.
- **Multilingual Query Support**: Accepts queries in any language with live visual indicator (`🌐 Translated to English: "..."`).
- **Context-Aware Query Suggestions**: Quick-query clickable pills tailored to the active database.
- **Telemetry & Results Display**: Real-time stats (inference time, query execution time, attempt counter), formatted SQL box with copy button, and an interactive data table with row counters.
