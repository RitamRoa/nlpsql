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

Install the Python dependency:

```bash
python -m pip install --upgrade pip
python -m pip install llama-cpp-python
```

`app.py` imports `llama_cpp` to run the GGUF model. If you already have a compatible `llama-cpp-python` wheel or installation, you can use it instead of rebuilding the package.

## Run

From the project directory, start the server:

```bash
python app.py
```

Then open [http://localhost:8000](http://localhost:8000) in a browser. The first startup loads the local model into memory. Press `Ctrl+C` in the terminal to stop the server.

If port 8000 is already in use, stop the other process before starting this app; the port is currently fixed in `app.py`.

## Try it

Enter a question such as “List all employees in Sales” or choose one of the dashboard suggestions. The app displays the generated SQL, query results, and approximate model/query times.

The database is created in memory each time the app starts, with sample `employees`, `departments`, and `sales` tables. Any data changes disappear when the server stops.

## Troubleshooting

- **Model file not found:** put `SmolLM2-135M-text-to-sql.Q8_0.gguf` in the same directory as `app.py`, preserving the exact filename.
- **`No module named llama_cpp`:** activate the virtual environment and install `llama-cpp-python` with `python -m pip install llama-cpp-python`.
- **Package installation/build fails:** check that your Python version and platform are supported by the available wheels. If pip tries to compile from source, install the required C++ build tools and retry.
- **Server starts but the model is slow or memory is limited:** inference runs on CPU with six threads as configured in `app.py`; startup and query time depend on your hardware.

## API

The dashboard sends a JSON `POST` request to `/api/query`:

```json
{"prompt": "List all employees in Sales"}
```

The response includes the generated SQL, result columns and rows, any database error, and timing values. `GET /` serves the dashboard.
