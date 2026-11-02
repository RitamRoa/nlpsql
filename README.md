# Local Text-to-SQL Dashboard

A small Python web app that uses a local SmolLM2 GGUF model to turn natural-language questions into SQL, then runs the SQL against an in-memory SQLite database. The dashboard and model run locally; the first model load can take a little while.

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

To run the benchmark instead, use `python run_benchmark.py` from the project directory. It uses the mock provider when the GGUF model is not present.

If port 8000 is already in use, stop the other process before starting this app; the port is currently fixed in `app.py`.

## Try it

Enter a question such as “List all employees in Sales” or choose one of the dashboard suggestions. The app displays the generated SQL, query results, and approximate model/query times.

Query results include an automatic chart recommendation with bar, line, pie, and scatter options, a plain-English summary, and a sortable, searchable, paginated table. Use the result toolbar to download CSV or JSON, and the dataset bar's theme button to switch between dark and light mode. Chart recommendations use the first suitable category and numeric columns; charts are limited to the first 20 returned rows for readability.

The dashboard uses the pastel palette `#EDE8D0` (cream), `#D0EDDA` (mint), `#D0D5ED` (periwinkle), and `#EDD0E4` (blush), with dark ink for readable text. The light theme is used by default, with a coordinated dark theme; the theme selection is saved in browser storage.

The dashboard also includes autocomplete from schema columns and recent questions, grouped examples, suggested follow-ups, local query history/favorites, voice input in English (India), Hindi, and Kannada, and text-to-speech when supported by the browser. Voice input first tries the browser Web Speech API; if its speech service is unavailable, the microphone button falls back to local Whisper transcription when installed. To enable the local fallback, install `requirements-voice.txt` in the active environment and restart the app. On first transcription, faster-whisper downloads the configured model (`tiny` by default; set `WHISPER_MODEL` to change it). Audio recordings are sent only to this app's local server for transcription. `Ctrl+K` opens the command palette; `Ctrl+Enter` runs the current question. History, favorites, theme, and pinned result snapshots stay in browser storage on this device. Share links carry only the schema name and question; they rerun the query when opened if that schema is available in the running app and do not include saved result data.

Generated SQL can be edited and re-executed, but the server validates it as a single read-only `SELECT`. The repair loop shows both its initial and final SQL. Result actions include Excel-compatible SpreadsheetML (`.xls`) export, a print dialog for saving a PDF, and pinning results locally. Chart edits support chart types and sort direction; state/city maps use a small built-in India location set, not an online geocoder.

Schema uploads support up to eight files, with a combined 20 MB decoded limit (8 MB per file). CSV/JSON previews show inferred types and sample rows; choose INTEGER, REAL, or TEXT overrides before import. Multi-file uploads infer simple `<table>_id` links for joins and ER diagrams. Click a schema table to preview its first ten rows. Image uploads use local Tesseract OCR when `tesseract` is installed and on `PATH`; review and format the recognized text as CSV before importing. If Tesseract is unavailable, the app reports that requirement rather than sending your image to an external service. PDF export uses the browser's print-to-PDF dialog.

Query stages stream to the dashboard as the pipeline runs. Database files can be connected read-only using SQLite or DuckDB. PostgreSQL/MySQL support uses SQLAlchemy with psycopg/PyMySQL; use a read-only database account and a connection string. The web server binds to `127.0.0.1`; remote database connection strings are kept only in server memory while the app runs and are not written to query history or share links. Browser Web Speech recognition may use browser/platform services; local Whisper is an optional fallback.

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
