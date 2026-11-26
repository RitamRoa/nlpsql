# Local Text-to-SQL Dashboard

A Python web app that turns natural-language questions into SQL with a small local model (SmolLM2 GGUF by default), validates the SQL, and runs it read-only against SQLite, DuckDB, Postgres or MySQL. Around the model it adds schema retrieval, few-shot retrieval, a repair loop, confidence-based abstention, role-based access control and an approved-write workflow.

## Status and limits

- Accuracy is measured only by the held-out benchmark below. Run it with your model; no number in this repo should be read as a result from your setup.
- The bundled databases are small demos. Execution match on them is weaker evidence than on Spider or BIRD.
- Non-English questions use an offline word list by default. Online translation sends the question to a third-party API and is off unless `NLPSQL_ONLINE_TRANSLATION=1`.
- The access-control layer is defence in depth. Also give the database account least-privilege permissions.
- The HTTP server handles connections with a thread per request and binds to localhost by default. Model generation is serialized around the shared pipeline. Docker binds inside the container; expose it only to trusted users and configure access controls before doing so.

## Web UI

`python app.py` serves a single-page studio (sources in `ui/`: `index.html`, `studio.css`, `studio.js`; no build step).

- **Verification panel** beside every answer: confidence, whether the query was validated read-only, which tables were used, and what access control masked or filtered. Low-confidence answers are withheld, with the reason shown.
- **Live pipeline stepper** (server-sent events), clarification prompts, follow-up suggestions, and a Table / Chart / SQL / Explain / Review tab set.
- Light and dark themes, keyboard shortcuts (`?` lists them), arrow-key tab navigation, a mobile drawer, reduced-motion and print styles.
- The page is loaded once at import, so restart the server after editing `ui/`.

## Benchmark

`eval/heldout_benchmark.json` holds 80 questions with gold SQL (4 schemas x 20, easy/medium/hard). The harness refuses to run if a question or its gold SQL appears in the few-shot examples or semantic-layer files, builds pipelines without saved feedback, and runs ablations that each switch off one component.

```bash
python run_heldout_benchmark.py                       # all ablations, real model required
python run_heldout_benchmark.py --configs full baseline_minimal --domain company
python run_heldout_benchmark.py --out results.json --markdown results.md
```

Output: execution accuracy with a 95% interval, valid-SQL rate, coverage and selective accuracy, a confidence-vs-accuracy table, and per-difficulty and per-schema breakdowns. `run_benchmark.py` is a 5-question smoke test whose questions overlap the few-shot examples; do not quote it as accuracy.

### Results (llama3.2 via Ollama, 80 questions, 25 Nov 2026)

These are one run each of a sampled pipeline; repeated runs moved by a few points, so read differences under about 5 points as noise. They were measured before the SQL-extraction fix described below and should be re-run.

| Config | EX % | 95% CI | Valid SQL % |
|---|---|---|---|
| full | 82.5 | 72.7-89.3 | 91.2 |
| no_schema_pruning | 78.8 | 68.6-86.3 | 93.8 |
| no_confidence | 80.0 | 70.0-87.3 | 91.2 |
| baseline_minimal | 66.2 | 55.4-75.7 | 83.8 |

- The full pipeline beats the bare model by about 16 points (19 questions only `full` got right, 6 only the baseline got right).
- Schema pruning and confidence sampling add little here; their differences are within noise.
- Answers whose sampled candidates all agreed (confidence 1.0) were right 95.1% of the time (58 of 61); answers with confidence 0.667 were right 66.7% (8 of 12). Abstaining below full agreement (`strict_abstain` config) would answer 61 of 80 questions at that accuracy.
- Of the 14 wrong answers in `full`, 5 were repair attempts where the model returned an explanation instead of SQL, 3 returned correct logic with an extra column, and the rest were wrong joins, tables or date arithmetic.

Tests: `pytest` (set `MOCK_LLM=1` to avoid loading a model).

## Project files

- `app.py` — starts the HTTP server, initializes the sample SQLite database, loads the model, and serves the dashboard/API.
- `SmolLM2-135M-text-to-sql.Q8_0.gguf` — the model file expected beside `app.py` (about 145 MB).
- `.gitignore` — excludes the model file from Git.

## Requirements

- Python 3.10 or later (64-bit recommended).
- The GGUF model file in the project root, named exactly `SmolLM2-135M-text-to-sql.Q8_0.gguf`.
- A C/C++ build toolchain may be needed if a compatible `llama-cpp-python` wheel is unavailable for your Python/OS. On Windows, install Visual Studio Build Tools with the C++ workload; on macOS/Linux, install the platform's compiler tools.

## Setup

Open a terminal in the project directory. Create and activate a virtual environment:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, either allow scripts for the current terminal with `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` and activate again, or use the environment's Python directly as shown below.

On macOS/Linux, use:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the Python dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install llama-cpp-python
```

The requirements file installs the dependencies used by the app, benchmark, and optional PDF schema import. `llama-cpp-python` is separate because it may need to be built locally; install it to run the GGUF model. If you already have a compatible wheel or installation, you can use it instead of rebuilding the package.

## Run

From the project directory, start the server:

```bash
python app.py
```

Then open [http://localhost:8000](http://localhost:8000) in a browser. The first startup loads the local model into memory. Press `Ctrl+C` in the terminal to stop the server.

### Docker

The image starts in mock mode unless you configure a real provider. Build and run it locally:

```powershell
docker build -t nlpsql .
docker run --rm -p 8000:8000 nlpsql
```

The container listens on port 8000. The Docker image sets `NLPSQL_HOST=0.0.0.0` so Docker port forwarding works; keep the published port on a trusted local network. To use a GGUF model, mount the ignored model file at `/app/SmolLM2-135M-text-to-sql.Q8_0.gguf`, install a compatible `llama-cpp-python` build in the image, and set `MOCK_LLM=0`. Ollama can instead be selected with `OLLAMA_HOST` and `OLLAMA_MODEL`; ensure the container can reach that host.

To run the benchmark instead, use `python run_heldout_benchmark.py` (see Benchmark above).

If port 8000 is already in use, stop the other process before starting this app; the port is currently fixed in `app.py`.

## Try it

Enter a question such as “List all employees in Sales” or choose one of the dashboard suggestions. The app displays the generated SQL, query results, and approximate model/query times.

Query results include an automatic chart recommendation with bar, line, pie, and scatter options, a plain-English summary, and a sortable, searchable, paginated table. Use the result toolbar to download CSV or JSON, and the dataset bar's theme button to switch between dark and light mode. Chart recommendations use the first suitable category and numeric columns; charts are limited to the first 20 returned rows for readability.

The dashboard uses the pastel palette `#EDE8D0` (cream), `#D0EDDA` (mint), `#D0D5ED` (periwinkle), and `#EDD0E4` (blush), with dark ink for readable text. The light theme is used by default, with a coordinated dark theme; the theme selection is saved in browser storage.

The dashboard also includes autocomplete from schema columns and recent questions, grouped examples, suggested follow-ups, local query history/favorites, voice input in English (India), Hindi, and Kannada, and text-to-speech when supported by the browser. Voice input first tries the browser Web Speech API; if its speech service is unavailable, the microphone button falls back to local Whisper transcription when installed. To enable the local fallback, install `requirements-voice.txt` in the active environment and restart the app. On first transcription, faster-whisper downloads the configured model (`tiny` by default; set `WHISPER_MODEL` to change it). Audio recordings are sent only to this app's local server for transcription. `Ctrl+K` opens the command palette; `Ctrl+Enter` runs the current question. History, favorites, theme, and pinned result snapshots stay in browser storage on this device. Share links carry only the schema name and question; they rerun the query when opened if that schema is available in the running app and do not include saved result data.

Generated SQL can be edited and re-executed, but the server validates it as a single read-only `SELECT`. The repair loop shows both its initial and final SQL. Result actions include Excel-compatible SpreadsheetML (`.xls`) export, a print dialog for saving a PDF, and pinning results locally. Chart edits support chart types and sort direction; state/city maps use a small built-in India location set, not an online geocoder.

Schema uploads support up to eight files, with a combined 20 MB decoded limit (8 MB per file). CSV/JSON previews show inferred types and sample rows; choose INTEGER, REAL, or TEXT overrides before import. Multi-file uploads infer simple `<table>_id` links for joins and ER diagrams. Click a schema table to preview its first ten rows. Image uploads use local Tesseract OCR when `tesseract` is installed and on `PATH`; review and format the recognized text as CSV before importing. If Tesseract is unavailable, the app reports that requirement rather than sending your image to an external service. PDF export uses the browser's print-to-PDF dialog.

Query stages stream to the dashboard as the pipeline runs. Database files can be connected read-only using SQLite or DuckDB. PostgreSQL/MySQL support uses SQLAlchemy with psycopg/PyMySQL; use a read-only database account and a connection string. The web server binds to `127.0.0.1`; remote database connection strings are kept only in server memory while the app runs and are not written to query history or share links. Browser Web Speech recognition may use browser/platform services; local Whisper is an optional fallback.

## Semantic layer

Built-in schema metadata lives in editable YAML files under `data/semantic/`. The loader uses their table/column descriptions, synonyms, metric definitions, and business rules to ground retrieval and SQL generation. Generate or refresh documentation and starter `SUM`/`AVG` metrics for a built-in schema with:

```powershell
python -m core.semantic_layer --schema company --force
```

Omit `--force` to create a semantic file only when one does not already exist. Regeneration retains existing descriptions, synonyms, metrics, and business rules where the schema still contains those tables and columns. Review generated metric definitions and descriptions for your domain before relying on them. Add aliases or edit definitions directly in the YAML; an unknown table or column in the catalog is reported when building the pipeline.

## Query assistance

- Each natural-language query is generated and executed against up to three candidate SQL statements. The confidence score is the fraction of candidates agreeing on the exact returned result; the default acceptance threshold is `2/3`. Below that, the app abstains and reports that it is not sure. This is a result-agreement heuristic, not a calibrated probability of correctness.
- If a phrase matches multiple semantic synonyms, the dashboard asks which table/column was intended before running SQL. Database values can be linked fuzzily; those matches are marked as suggestions for the model to verify.
- Follow-up prompts can use the previous SQL and schema as conversation context. The dashboard also provides a deterministic SQL explanation/column trace, a narration computed from returned rows, and an `EXPLAIN` plan with scan/join warnings.
- SQL can be transpiled to SQLite, PostgreSQL, MySQL, or DuckDB. Transpilation does not execute the translated query.
- The model arena compares up to three configured providers. To report execution accuracy, supply a reference SQL query; without one, the leaderboard reports execution success only. This is not a held-out benchmark.
- Feedback is stored per role and schema locally in `%LOCALAPPDATA%\\NLPSQL\\feedback.sqlite3` (or under the user's home directory when `LOCALAPPDATA` is unset). Helpful examples are added only to requests for that same role and schema. Older unscoped entries are assigned to `local_operator` during migration. Corrected feedback replays the question and records same-question before/after result agreement; it is not a held-out accuracy estimate. Feedback records include the question and corrected SQL, so protect this local file.

## Access controls and safe writes

Role-based controls are optional. To enable them, create an access policy YAML file, then set the policy path and a token-to-role JSON mapping before starting the app:

```yaml
roles:
  analyst:
    schemas: [company]
    tables:
      employees:
        columns: [id, name, department, email]
        masked_columns: [email]
        row_filter: "department = 'Sales'"
      sales:
        columns: [id, employee_id, amount, region]
```

In PowerShell:

```powershell
$env:NLPSQL_ACCESS_POLICY = "C:\\path\\to\\access-policy.yaml"
$env:NLPSQL_ROLE_TOKENS = '{"replace-with-a-long-random-token":"analyst"}'
python app.py
```

Sign in through the dashboard with the token. Use real randomly generated secrets; never commit them. Omit a table's `columns` list to allow all its columns, and omit `row_filter`/`masked_columns` when they are not needed. Row filters are SQL predicates validated against the database schema and automatically applied. Masked columns cannot be used for filtering, sorting, or calculations. The app binds to localhost by default; do not expose it to untrusted networks without adding deployment-grade authentication, TLS, and operational controls.

Queries are validated as read-only SQL before execution. `/api/security_report` runs a deterministic regression suite against the active schema and reports the SQL-boundary block rate, including stored-value prompt-injection neutralization. This is not a live attack-rate measurement against the configured LLM.

The audit database defaults to `%LOCALAPPDATA%\\NLPSQL\\audit.sqlite3` (or the user's home directory); set `NLPSQL_AUDIT_DB` to choose another path. It records hashes of questions and SQL rather than their raw contents.

Write previews are restricted to in-memory SQLite. A role must set `allow_writes: true`; only a single `INSERT` or `UPDATE` is eligible. The preview runs in a rolled-back transaction and returns a row diff. Explicit confirmation commits once using a short-lived, single-use approval token. Writes to database files or remote databases, row-filtered tables, and tables with triggers/foreign-key dependencies are not supported. Use these previews only with trusted local users and test data.

## MCP server

NLPSQL exposes the validated, read-only query pipeline through an MCP stdio server. Start it from the project directory:

```powershell
$env:DB_SCHEMA = "company"
$env:MOCK_LLM = "1" # Optional; otherwise the configured provider is used.
python -m core.mcp_server
```

Configure the MCP client to launch that command with the project directory as its working directory. `DB_ENGINE` and `DB_PATH` select a connected SQLite or DuckDB database. When role policies are enabled, also set `NLPSQL_ACCESS_POLICY` and `NLPSQL_MCP_ROLE` to the permitted role name in the MCP server process. The stdio server is intended for a trusted local MCP client; it does not perform interactive token login. Configure a restricted role before exposing access to sensitive data. Audit events use `NLPSQL_AUDIT_DB`.

## Model providers and arena

The default provider selection prefers a local GGUF model when present, then an explicitly configured Ollama host, then an API provider when an API key is configured; otherwise it uses the mock provider. The dashboard server currently supports local GGUF and Ollama providers; the standalone benchmark provider factory also supports HTTP API providers. Ollama uses `OLLAMA_HOST` and `OLLAMA_MODEL`. The API provider uses `LLM_API_KEY` or `OPENAI_API_KEY`, with optional `LLM_API_MODEL` and `LLM_API_ENDPOINT`.

The arena can use up to three providers defined by `NLPSQL_ARENA_MODELS` as a JSON array. For example:

```powershell
$env:NLPSQL_ARENA_MODELS = '[{"name":"Local","type":"local","path":"C:\\models\\model.gguf"},{"name":"Ollama","type":"ollama","host":"http://localhost:11434","model":"llama3.1:8b"},{"name":"API","type":"api","model":"gpt-4o-mini","endpoint":"https://api.openai.com/v1/chat/completions"}]'
```

Supported `type` values are `local`, `ollama`, and `api`; API keys are read from the environment, not this JSON. Only configure providers you intend to run. The arena sends the question and visible schema to each configured provider.

The database is created in memory each time the app starts, with sample `employees`, `departments`, and `sales` tables. Any data changes disappear when the server stops.

## Troubleshooting

- **Model file not found:** put `SmolLM2-135M-text-to-sql.Q8_0.gguf` in the same directory as `app.py`, preserving the exact filename.
- **`No module named llama_cpp`:** activate the virtual environment and install `llama-cpp-python` with `python -m pip install llama-cpp-python`.
- **Package installation/build fails:** check that your Python version and platform are supported by the available wheels. If pip tries to compile from source, install the required C++ build tools and retry.
- **Server starts but the model is slow or memory is limited:** inference runs on CPU with six threads as configured in `app.py`; startup and query time depend on your hardware.
- **Browser voice input reports a network error:** allow the browser speech service to reach its network service, or install the optional local fallback with `python -m pip install -r requirements-voice.txt`. Restart the app after installation; the first local transcription downloads the Whisper model.

## API

The dashboard sends a JSON `POST` request to `/api/query`:

```json
{"prompt": "List all employees in Sales"}
```

The response includes the generated SQL, result columns and rows, any database error, and timing values. `GET /` serves the dashboard.

## License

MIT License; the holder is listed as `RitamRoa` in `LICENSE` (matching the repository owner). See `LICENSE`. Confirm the holder is the rights owner before redistribution.
