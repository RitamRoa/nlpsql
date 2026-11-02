import base64
import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import urllib.parse
import sys
import io
from decimal import Decimal
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from http.server import BaseHTTPRequestHandler, HTTPServer

from core.generator import SQLGenerator
from core.providers.mock import MockProvider
from core.providers.ollama import OllamaProvider
from core.loader import build_pipeline, list_available_schemas
from core.schema_importer import import_schema
from core.adapters.sqlite import SQLiteAdapter
from core.voice import transcribe_local_audio

MODEL_PATH = os.environ.get("MODEL_PATH", "SmolLM2-135M-text-to-sql.Q8_0.gguf")
DB_ENGINE = os.environ.get("DB_ENGINE", "sqlite").lower()
DB_PATH = os.environ.get("DB_PATH")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "company")

# LLM Provider Selection
OLLAMA_HOST = os.environ.get("OLLAMA_HOST")
if OLLAMA_HOST:
    print(f"Connecting to Ollama provider at {OLLAMA_HOST}")
    provider = OllamaProvider(host=OLLAMA_HOST)
elif os.path.exists(MODEL_PATH) and os.environ.get("MOCK_LLM", "0") != "1":
    print(f"Loading local GGUF model: {MODEL_PATH}")
    provider = SQLGenerator(model_path=MODEL_PATH)
else:
    print("Notice: Starting with MockProvider for local testing.")
    provider = MockProvider()

# Build initial pipeline
current_schema_name = DB_SCHEMA
current_db_path = DB_PATH
current_db_engine = DB_ENGINE
pipeline, adapter, knowledge_engine = build_pipeline(
    db_engine=DB_ENGINE,
    db_path=current_db_path,
    schema_name=current_schema_name,
    provider=provider,
)

# Uploaded custom schemas: {display_name: seed_sql}
uploaded_schemas: dict[str, str] = {}
uploaded_relationships: dict[str, list[dict[str, str]]] = {}
connected_databases: dict[str, tuple[str, str]] = {}


def _infer_relationships(schema) -> list[dict[str, str]]:
    table_names = {table.name.lower(): table.name for table in schema.tables}
    relationships = []
    for table in schema.tables:
        for column in table.columns:
            match = re.fullmatch(r"(.+)_id", column.name, re.IGNORECASE)
            if not match:
                continue
            prefix = match.group(1).lower()
            candidates = (prefix, prefix + "s", prefix.rstrip("s"))
            target = next((table_names[name] for name in candidates if name in table_names), None)
            if target and target != table.name:
                relationships.append({
                    "table": table.name,
                    "column": column.name,
                    "ref_table": target,
                    "ref_column": "id",
                })
    return relationships


def _decode_upload_files(params: dict) -> list[tuple[str, bytes, dict[str, str]]]:
    files = params.get("files")
    if files is None:
        files = [{
            "filename": params.get("filename", "upload"),
            "content_b64": params.get("content_b64", ""),
            "type_overrides": params.get("type_overrides", {}),
        }]
    if not isinstance(files, list) or not files or len(files) > 8:
        raise ValueError("Upload between 1 and 8 files at a time.")
    decoded = []
    total_bytes = 0
    for file in files:
        if not isinstance(file, dict) or not isinstance(file.get("filename"), str):
            raise ValueError("Each uploaded file needs a filename and base64 content.")
        try:
            content = base64.b64decode(file.get("content_b64", ""), validate=True)
        except (ValueError, TypeError) as error:
            raise ValueError(f"Invalid base64 content for {file.get('filename')}.") from error
        if not content:
            raise ValueError(f"{file['filename']} is empty.")
        if len(content) > 8 * 1024 * 1024:
            raise ValueError(f"{file['filename']} exceeds the 8 MB per-file limit.")
        total_bytes += len(content)
        overrides = file.get("type_overrides", {})
        if not isinstance(overrides, dict):
            raise ValueError(f"Invalid column type overrides for {file['filename']}.")
        decoded.append((file["filename"], content, overrides))
    if total_bytes > 20 * 1024 * 1024:
        raise ValueError("Combined uploads cannot exceed 20 MB.")
    return decoded


def _prepare_uploaded_schema(params: dict) -> tuple[
    str, list[str], list[dict[str, str]], list[int]
]:
    files = _decode_upload_files(params)
    seed_parts = []
    table_names = []
    table_sources = []
    for file_index, (filename, content, overrides) in enumerate(files):
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension in {"png", "jpg", "jpeg"}:
            raise ValueError("Images must be sent to the OCR endpoint before importing.")
        seed_sql, names = import_schema(
            filename,
            content,
            type_overrides=overrides,
        )
        seed_parts.append(seed_sql)
        table_names.extend(names)
        table_sources.extend([file_index] * len(names))
    seed_sql = "\n\n".join(seed_parts)
    preview = SQLiteAdapter(seed_sql=seed_sql)
    try:
        relationships = _infer_relationships(preview.get_schema())
    finally:
        preview.close()
    return seed_sql, table_names, relationships, table_sources


def _build_connection_pipeline(engine: str, connection: str, query_provider):
    if engine in {"sqlite", "duckdb"}:
        return build_pipeline(
            db_engine=engine,
            db_path=connection,
            schema_name="company",
            provider=query_provider,
        )
    from core.adapters.relational import RelationalAdapter
    from core.agents.classifier import QueryClassifier
    from core.agents.critic import SemanticCritic
    from core.agents.drafter import SQLDrafter
    from core.few_shot import FewShotRetriever
    from core.pipeline import Pipeline
    from core.retriever import SchemaRetriever

    relational_adapter = RelationalAdapter(connection)
    try:
        relational_adapter.get_schema()
        drafter = SQLDrafter(
            provider=query_provider,
            few_shot_retriever=FewShotRetriever(examples=[], top_k=2),
        )
        return (
            Pipeline(
                adapter=relational_adapter,
                generator=drafter,
                retriever=SchemaRetriever(),
                classifier=QueryClassifier(),
                critic=SemanticCritic(),
            ),
            relational_adapter,
            None,
        )
    except Exception:
        relational_adapter.close()
        raise


def _redact_connection_error(error: Exception) -> str:
    return re.sub(r"(://[^:/@]+:)[^@]+@", r"\1***@", str(error))


def _json_rows(rows) -> list[list]:
    serialized = []
    for row in rows:
        values = []
        for value in row:
            if value is None or isinstance(value, (str, int, float, bool)):
                values.append(value)
            elif isinstance(value, (datetime.date, datetime.time, datetime.datetime)):
                values.append(value.isoformat())
            elif isinstance(value, Decimal):
                values.append(str(value))
            elif isinstance(value, bytes):
                values.append({"base64": base64.b64encode(value).decode("ascii")})
            else:
                values.append(str(value))
        serialized.append(values)
    return serialized


SCHEMA_SUGGESTIONS = {
    "company": [
        "List all employees in Engineering",
        "What is the average salary by department?",
        "Show all remote employees",
        "Which projects are currently active?",
        "Show top performers by performance rating",
        "How many leave days has each employee taken?",
    ],
    "ecommerce": [
        "List all Gold tier customers",
        "What are the top 5 most expensive products?",
        "Show total revenue by category",
        "Which orders are still processing?",
        "Show all product reviews with ratings",
        "What is the average order value by payment method?",
    ],
    "university": [
        "List all Computer Science students",
        "What is the average GPA by major?",
        "Show students who received scholarships",
        "How many students are enrolled in each course?",
        "List all professors and their departments",
        "Show students with the highest attendance",
    ],
    "healthcare": [
        "List all cardiologists",
        "Show patients currently admitted",
        "Which patients have no insurance?",
        "What are the most prescribed medications?",
        "Show the total bill per patient from admissions",
        "List all doctors by specialization",
    ],
}


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NLPSQL Studio — Natural language analytics</title>
    <meta name="description" content="Query any database using natural language. Upload your own schema or use built-in datasets.">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg:       #080c14;
            --card:     rgba(14, 20, 36, 0.85);
            --border:   rgba(255,255,255,0.07);
            --border-a: rgba(99,157,255,0.4);
            --tp:       #e8ecf4;
            --ts:       #8892a4;
            --tm:       #4b5565;
            --acc:      #4f8ef7;
            --acc-d:    #1a56db;
            --acc-g:    rgba(79,142,247,0.28);
            --ok:       #10b981;
            --err:      #f87171;
            --warn:     #fbbf24;
            --purple:   #a78bfa;
            --mono:     'JetBrains Mono', monospace;
            --pill:     rgba(79,142,247,0.12);
        }

        body.theme-light {
            --bg: #f5f7fa;
            --card: #ffffff;
            --border: #dce3ec;
            --border-a: #a9bfdf;
            --tp: #172b45;
            --ts: #52647a;
            --tm: #75849a;
            --acc: #245fb5;
            --acc-d: #194b91;
            --acc-g: rgba(36,95,181,.16);
            --pill: #edf3fb;
            background-image: radial-gradient(ellipse 80% 50% at 50% -10%, rgba(36,95,181,.09) 0%, transparent 70%);
        }

        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: 'Outfit', sans-serif;
            background: var(--bg);
            color: var(--tp);
            min-height: 100vh;
            display: flex; flex-direction: column; align-items: center;
            padding: 2rem 1rem 5rem;
            background-image:
                radial-gradient(ellipse 80% 50% at 50% -10%, rgba(59,92,200,.18) 0%, transparent 70%),
                radial-gradient(ellipse 50% 30% at 80% 80%, rgba(30,58,138,.1) 0%, transparent 60%);
            overflow-x: hidden;
        }

        .wrap { width: 100%; max-width: 1220px; display: flex; flex-direction: column; gap: 1.75rem; }

        /* ── Header ───────────────────────── */
        header { text-align: center; padding: .5rem 0; animation: fadeIn .7s ease-out; }
        .eyebrow { font-size: .75rem; font-weight: 600; letter-spacing: .15em; text-transform: uppercase; color: var(--acc); margin-bottom: .5rem; }
        h1 {
            font-size: clamp(1.9rem,5vw,3rem); font-weight: 800;
            background: linear-gradient(135deg,#93c5fd 0%,#4f8ef7 50%,#6366f1 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
            margin-bottom: .5rem; line-height: 1.1;
        }
        header p { color: var(--ts); font-size: 1rem; }

        /* ── Schema Bar ───────────────────── */
        .schema-bar {
            background: var(--card); border: 1px solid var(--border);
            border-radius: 14px; padding: .85rem 1.2rem;
            display: flex; align-items: center; gap: .9rem; flex-wrap: wrap;
            backdrop-filter: blur(16px); box-shadow: 0 4px 24px rgba(0,0,0,.3);
        }
        .bar-label { font-size: .75rem; font-weight: 600; color: var(--ts); text-transform: uppercase; letter-spacing: .07em; white-space: nowrap; }
        .schema-tabs { display: flex; gap: .45rem; flex-wrap: wrap; flex: 1; }
        .stab {
            background: rgba(255,255,255,.04); border: 1px solid var(--border);
            padding: .38rem .85rem; border-radius: 8px; color: var(--ts);
            font-size: .88rem; font-family: inherit; cursor: pointer;
            transition: all .18s; text-transform: capitalize; font-weight: 500;
        }
        .stab:hover { background: var(--pill); color: #93c5fd; border-color: rgba(99,157,255,.25); }
        .stab.active { background: linear-gradient(135deg,rgba(79,142,247,.22),rgba(99,102,241,.18)); color: #93c5fd; border-color: var(--border-a); font-weight: 700; box-shadow: 0 0 12px rgba(79,142,247,.15); }
        .stab.custom { color: var(--purple); border-color: rgba(167,139,250,.3); }
        .stab.custom.active { background: linear-gradient(135deg,rgba(167,139,250,.2),rgba(99,102,241,.15)); color: var(--purple); border-color: rgba(167,139,250,.5); }

        .upload-btn {
            margin-left: auto; padding: .38rem .9rem; border-radius: 8px;
            background: rgba(167,139,250,.1); border: 1px solid rgba(167,139,250,.3);
            color: var(--purple); font-size: .85rem; font-family: inherit;
            cursor: pointer; transition: all .18s; white-space: nowrap; font-weight: 600;
        }
        .upload-btn:hover { background: rgba(167,139,250,.2); box-shadow: 0 0 12px rgba(167,139,250,.15); transform: translateY(-1px); }

        .status-dot {
            font-size: .75rem; font-family: var(--mono); color: var(--ok);
            display: flex; align-items: center; gap: .3rem; white-space: nowrap;
        }
        .status-dot::before { content:''; width:7px; height:7px; border-radius:50%; background:var(--ok); box-shadow:0 0 6px var(--ok); display:inline-block; }
        .status-dot.busy { color:var(--warn); }
        .status-dot.busy::before { background:var(--warn); box-shadow:0 0 6px var(--warn); animation:pulse 1s ease-in-out infinite; }
        .theme-btn, .tool-btn, .table-control, .chart-select {
            border:1px solid var(--border); border-radius:8px; padding:.42rem .68rem;
            background:rgba(127,127,127,.08); color:var(--tp); font:inherit; font-size:.82rem;
        }
        #prompt, #sqlOut { width:100%; }
        #prompt-list { display:none; }
        .query-tools { display:flex; gap:.4rem; position:absolute; right:.45rem; top:50%; transform:translateY(-50%); }
        .query-tools button { border:0; background:transparent; color:var(--ts); cursor:pointer; padding:.25rem; font-size:1rem; }
        .query-tools button:hover { color:var(--tp); }
        .suggestion-group { display:flex; flex-direction:column; gap:.38rem; margin-top:.5rem; }
        .suggestion-title { color:var(--tm); font-size:.66rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase; }
        .progress-msg { display:none; color:var(--acc); padding:.55rem .7rem; border-radius:8px; background:var(--pill); font-size:.85rem; }
        .progress-msg.active { display:block; }
        .followups { display:flex; gap:.4rem; flex-wrap:wrap; }
        .sql-editor-wrap { position:relative; min-height:100px; }
        .sql-editor, .sql-highlight { width:100%; min-height:100px; margin:0; padding:.2rem; white-space:pre-wrap; overflow-wrap:anywhere; font:.88rem/1.5 var(--mono); tab-size:4; }
        .sql-editor { position:relative; resize:vertical; border:0; outline:0; background:transparent; color:transparent; caret-color:var(--tp); }
        .sql-editor::selection { background:rgba(79,142,247,.35); color:transparent; }
        .sql-highlight { position:absolute; inset:0; overflow:hidden; pointer-events:none; color:#34d399; }
        .sql-keyword { color:#93c5fd; font-weight:700; }
        .sql-string { color:#fbbf24; }
        .sql-number { color:#c4b5fd; }
        .sql-comment { color:#778397; font-style:italic; }
        .sql-actions { display:flex; justify-content:flex-end; gap:.4rem; margin-top:.5rem; }
        .diff-panel { display:none; padding:.7rem; border:1px solid var(--border); border-radius:8px; font: .78rem/1.5 var(--mono); overflow:auto; }
        .sql-diff { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.65rem; }
        .sql-diff pre { white-space:pre-wrap; overflow-wrap:anywhere; padding:.55rem; background:rgba(127,127,127,.06); border-radius:6px; }
        @media (max-width: 600px) { .sql-diff { grid-template-columns:1fr; } }
        .history-panel, .pin-panel { display:flex; flex-direction:column; gap:.6rem; }
        .saved-item { display:flex; align-items:center; gap:.5rem; justify-content:space-between; padding:.5rem .65rem; border:1px solid var(--border); border-radius:8px; color:var(--ts); }
        .saved-item button { color:var(--tp); background:none; border:0; cursor:pointer; text-align:left; font:inherit; }
        .schema-tools { display:flex; flex-wrap:wrap; gap:.4rem; }
        .preview-table { width:100%; overflow:auto; margin:.6rem 0; }
        .preview-table table { min-width:540px; }
        .er-canvas { width:100%; overflow:auto; border:1px solid var(--border); border-radius:8px; }
        .er-canvas svg { min-width:420px; width:100%; }
        .command-overlay { display:none; position:fixed; inset:0; z-index:200; background:rgba(0,0,0,.65); align-items:flex-start; justify-content:center; padding:15vh 1rem 1rem; }
        .command-overlay.open { display:flex; }
        .command-box { width:min(600px,95vw); padding:1rem; border-radius:14px; background:var(--card); border:1px solid var(--border-a); box-shadow:0 20px 70px rgba(0,0,0,.35); }
        .command-box input { width:100%; padding:.75rem; background:rgba(127,127,127,.08); color:var(--tp); border:1px solid var(--border); border-radius:8px; font:inherit; }
        .command-list { display:grid; gap:.4rem; margin-top:.6rem; }
        .command-list button { background:transparent; border:0; color:var(--tp); padding:.6rem; text-align:left; cursor:pointer; font:inherit; border-radius:6px; }
        .command-list button:hover { background:var(--pill); }
        .upload-preview { max-height:250px; overflow:auto; font-size:.8rem; }
        .type-select { background:var(--card); color:var(--tp); border:1px solid var(--border); border-radius:5px; padding:.2rem; }
        .geo-mark { fill:#f97316; stroke:#fff; stroke-width:1.5; }
        .geo-bg { fill:rgba(79,142,247,.08); stroke:var(--border-a); }
        .geo-grid { stroke:var(--border); stroke-dasharray:3 4; }
        .theme-btn, .tool-btn { cursor:pointer; transition:background .18s,border-color .18s; }
        .theme-btn:hover, .tool-btn:hover { background:var(--pill); border-color:var(--border-a); }
        .theme-btn { white-space:nowrap; }

        /* ── Grid ─────────────────────────── */
        .grid { display: grid; grid-template-columns: 270px 1fr; gap: 1.5rem; align-items: start; }
        @media (max-width: 860px) { .grid { grid-template-columns: 1fr; } }

        /* ── Cards ────────────────────────── */
        .card {
            background: var(--card); backdrop-filter: blur(16px);
            border: 1px solid var(--border); border-radius: 16px;
            padding: 1.35rem; box-shadow: 0 8px 32px rgba(0,0,0,.4);
        }

        /* ── Schema Panel ─────────────────── */
        .schema-panel { display:flex; flex-direction:column; gap:.85rem; max-height:660px; overflow-y:auto; }
        .schema-panel::-webkit-scrollbar { width:3px; }
        .schema-panel::-webkit-scrollbar-thumb { background:rgba(255,255,255,.1); border-radius:4px; }

        .panel-hdr { font-size:.75rem; font-weight:700; text-transform:uppercase; letter-spacing:.08em; color:var(--ts); border-bottom:1px solid var(--border); padding-bottom:.6rem; display:flex; align-items:center; justify-content:space-between; }
        .db-badge { background:var(--pill); color:var(--acc); font-size:.68rem; padding:.18rem .5rem; border-radius:5px; font-weight:600; text-transform:none; letter-spacing:0; border:1px solid rgba(79,142,247,.2); }

        .tbl-block { background:rgba(0,0,0,.22); border-radius:9px; padding:.65rem .75rem; border:1px solid rgba(255,255,255,.04); transition:border-color .2s; }
        .tbl-block:hover { border-color:rgba(99,157,255,.15); }
        .tbl-name { font-family:var(--mono); color:#f472b6; font-weight:600; font-size:.88rem; margin-bottom:.3rem; display:flex; align-items:center; gap:.35rem; }
        .tbl-name::before { content:'▸'; color:var(--tm); font-size:.72rem; }
        .col-list { font-family:var(--mono); font-size:.73rem; color:var(--ts); list-style:none; padding-left:.5rem; display:flex; flex-direction:column; gap:.13rem; }
        .col-list li { display:flex; align-items:center; gap:.28rem; }
        .col-list li::before { content:"·"; color:var(--acc); font-size:.95rem; line-height:1; }
        .cn { color:var(--tp); } .ct { color:var(--tm); }
        .pk { color:var(--warn); font-size:.62rem; padding:.03rem .28rem; background:rgba(251,191,36,.1); border-radius:3px; }
        .fk { color:var(--purple); font-size:.62rem; padding:.03rem .28rem; background:rgba(167,139,250,.1); border-radius:3px; }
        .loading-msg { color:var(--tm); font-size:.85rem; padding:1rem 0; text-align:center; animation:pulse 1.5s ease-in-out infinite; }

        /* ── Query Panel ──────────────────── */
        .query-panel { display:flex; flex-direction:column; gap:1.2rem; }
        .slabel { font-size:.76rem; font-weight:600; color:var(--ts); text-transform:uppercase; letter-spacing:.08em; margin-bottom:.1rem; }

        .inp-wrap { position:relative; margin-top:.45rem; }
        #prompt {
            width:100%; padding:.88rem 3rem .88rem 1.1rem;
            border-radius:12px; background:rgba(5,10,22,.7);
            border:1.5px solid var(--border); color:var(--tp);
            font-family:inherit; font-size:1rem; outline:none;
            transition:all .25s; box-shadow:inset 0 2px 8px rgba(0,0,0,.3);
        }
        #prompt:focus { border-color:var(--acc); box-shadow:0 0 20px var(--acc-g),inset 0 2px 8px rgba(0,0,0,.2); }
        #prompt::placeholder { color:var(--tm); }
        .inp-icon { position:absolute; right:1rem; top:50%; transform:translateY(-50%); color:var(--tm); pointer-events:none; font-size:1.1rem; }

        .suggestions { display:flex; flex-wrap:wrap; gap:.42rem; }
        .sug-btn {
            background:rgba(255,255,255,.04); border:1px solid var(--border);
            padding:.32rem .7rem; border-radius:7px; color:var(--ts);
            font-size:.8rem; font-family:inherit; cursor:pointer; transition:all .18s;
        }
        .sug-btn:hover { background:var(--pill); color:#93c5fd; border-color:rgba(99,157,255,.3); transform:translateY(-1px); box-shadow:0 2px 8px rgba(79,142,247,.1); }

        #runBtn {
            padding:.82rem 1.4rem; border-radius:12px;
            background:linear-gradient(135deg,var(--acc) 0%,var(--acc-d) 100%);
            color:#fff; border:none; font-weight:700; font-size:.95rem;
            font-family:inherit; cursor:pointer; transition:all .2s;
            box-shadow:0 4px 16px var(--acc-g);
            display:flex; align-items:center; justify-content:center; gap:.5rem; letter-spacing:.02em;
        }
        #runBtn:hover { transform:translateY(-2px); box-shadow:0 8px 24px var(--acc-g); filter:brightness(1.1); }
        #runBtn:active { transform:translateY(0); }
        #runBtn:disabled { background:#2a3347; cursor:not-allowed; box-shadow:none; transform:none; filter:none; }

        /* ── Results ──────────────────────── */
        .results { display:none; flex-direction:column; gap:1.15rem; margin-top:.4rem; animation:slideUp .35s ease-out; }
        .sql-box { background:#040609; border-radius:11px; padding:1rem 1.1rem; border:1px solid rgba(52,211,153,.15); position:relative; }
        .sql-box::before { content:"GENERATED SQL"; position:absolute; top:-8px; left:11px; background:#040609; padding:0 5px; font-size:.58rem; font-weight:700; letter-spacing:.12em; color:#34d399; }
        code { font-family:var(--mono); font-size:.88rem; color:#34d399; white-space:pre-wrap; word-break:break-all; display:block; }
        .stats { display:flex; gap:1.2rem; flex-wrap:wrap; font-size:.8rem; color:var(--ts); padding:.38rem .7rem; background:rgba(255,255,255,.025); border-radius:7px; width:fit-content; border:1px solid var(--border); }
        .stats span { color:var(--tp); font-weight:700; font-family:var(--mono); }
        .tbl-wrap { width:100%; overflow-x:auto; border-radius:11px; border:1px solid var(--border); }
        table { width:100%; border-collapse:collapse; text-align:left; font-size:.88rem; }
        th { background:rgba(5,12,26,.9); padding:.65rem .95rem; font-weight:600; font-size:.76rem; color:var(--ts); border-bottom:1px solid var(--border); text-transform:uppercase; letter-spacing:.04em; white-space:nowrap; }
        td { padding:.62rem .95rem; border-bottom:1px solid rgba(255,255,255,.04); color:var(--tp); }
        tr:last-child td { border-bottom:none; }
        tr:hover td { background:rgba(255,255,255,.014); }
        td em { color:var(--tm); font-style:italic; }
        .row-ct { font-size:.75rem; color:var(--tm); padding:.38rem .6rem; text-align:right; }
        .result-summary { color:var(--ts); line-height:1.55; padding:.7rem .85rem; border-left:3px solid var(--acc); background:var(--pill); border-radius:0 8px 8px 0; }
        .result-toolbar, .table-toolbar, .pagination { display:flex; align-items:center; gap:.55rem; flex-wrap:wrap; }
        .result-toolbar { justify-content:space-between; }
        .table-toolbar { justify-content:space-between; margin:.7rem 0; }
        .table-control { min-width:0; }
        .table-filter { width:min(280px,100%); }
        .pagination { justify-content:flex-end; color:var(--ts); font-size:.8rem; }
        .pagination .tool-btn:disabled { opacity:.45; cursor:not-allowed; }
        th[role="button"] { cursor:pointer; }
        th[role="button"]:hover { color:var(--tp); }
        .chart-panel { border:1px solid var(--border); border-radius:11px; padding:.8rem; background:rgba(127,127,127,.035); }
        .chart-heading { display:flex; align-items:center; justify-content:space-between; gap:.7rem; flex-wrap:wrap; margin-bottom:.6rem; color:var(--ts); font-size:.82rem; }
        .chart-canvas { width:100%; min-height:180px; overflow:hidden; }
        .chart-canvas svg { display:block; width:100%; height:auto; max-height:360px; }
        .chart-empty { color:var(--ts); padding:1.25rem .5rem; text-align:center; font-size:.85rem; }
        .chart-grid { stroke:var(--border); stroke-width:1; }
        .chart-label { fill:var(--ts); font:11px 'Outfit',sans-serif; }
        .chart-mark { fill:var(--acc); }
        .chart-line { fill:none; stroke:var(--acc); stroke-width:3; stroke-linecap:round; stroke-linejoin:round; }
        .theme-light .sql-box { background:#f6f8fb; }
        .theme-light .sql-box::before { background:#f6f8fb; }
        .theme-light th { background:#e9eef6; }
        .theme-light .tbl-block { background:rgba(15,23,42,.035); }
        @media (max-width: 600px) {
            body { padding:1rem .65rem 3rem; }
            .wrap { gap:1rem; }
            .card { padding:1rem; }
            .schema-bar { padding:.75rem; gap:.6rem; }
            .upload-btn { margin-left:0; }
            .result-toolbar, .table-toolbar { align-items:stretch; }
            .result-toolbar > *, .table-toolbar > * { flex:1 1 auto; }
            .pagination { justify-content:space-between; }
        }
        .err-box { background:rgba(248,113,113,.07); border:1px solid rgba(248,113,113,.2); color:#fca5a5; padding:.9rem 1rem; border-radius:11px; font-family:var(--mono); font-size:.83rem; line-height:1.5; }
        .empty-res { color:var(--ts); text-align:center; padding:1.6rem; font-size:.9rem; background:rgba(255,255,255,.014); border-radius:9px; }

        .spin { display:none; width:19px; height:19px; border:2.5px solid rgba(255,255,255,.15); border-radius:50%; border-top-color:#fff; animation:spin .8s linear infinite; }

        /* ── Upload Modal ─────────────────── */
        .overlay {
            display:none; position:fixed; inset:0; background:rgba(4,6,14,.75);
            backdrop-filter:blur(6px); z-index:100;
            align-items:center; justify-content:center;
        }
        .overlay.open { display:flex; animation:fadeIn .25s ease-out; }

        .modal {
            background:rgba(12,18,34,.97); border:1px solid var(--border-a);
            border-radius:20px; padding:2rem; width:min(560px,95vw);
            box-shadow:0 24px 80px rgba(0,0,0,.7);
            display:flex; flex-direction:column; gap:1.35rem;
            animation:slideUp .28s ease-out;
        }
        .modal-hdr { display:flex; align-items:center; justify-content:space-between; }
        .modal-hdr h2 { font-size:1.3rem; font-weight:700; background:linear-gradient(135deg,#c4b5fd,var(--purple)); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
        .close-btn { background:none; border:none; color:var(--ts); font-size:1.4rem; cursor:pointer; padding:.2rem .4rem; transition:color .15s; }
        .close-btn:hover { color:var(--tp); }

        .drop-zone {
            border:2px dashed rgba(167,139,250,.35); border-radius:14px;
            padding:2rem 1rem; text-align:center; cursor:pointer; transition:all .2s;
            background:rgba(167,139,250,.04); position:relative;
        }
        .drop-zone.drag-over { border-color:var(--purple); background:rgba(167,139,250,.1); box-shadow:0 0 20px rgba(167,139,250,.15); }
        .drop-zone input[type=file] { position:absolute; inset:0; opacity:0; cursor:pointer; width:100%; height:100%; }
        .drop-icon { font-size:2.5rem; margin-bottom:.5rem; }
        .drop-title { font-size:1rem; font-weight:600; color:var(--tp); margin-bottom:.3rem; }
        .drop-sub { font-size:.82rem; color:var(--ts); }
        .drop-types { display:flex; gap:.4rem; justify-content:center; margin-top:.6rem; flex-wrap:wrap; }
        .type-chip { background:rgba(255,255,255,.06); border:1px solid var(--border); border-radius:5px; padding:.18rem .55rem; font-size:.73rem; font-family:var(--mono); color:var(--ts); }

        .file-preview {
            display:none; background:rgba(167,139,250,.08); border:1px solid rgba(167,139,250,.25);
            border-radius:10px; padding:.75rem 1rem;
            align-items:center; gap:.7rem;
        }
        .file-preview.show { display:flex; }
        .file-icon { font-size:1.4rem; }
        .file-info { flex:1; min-width:0; }
        .file-name { font-weight:600; font-size:.9rem; color:var(--tp); truncate; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .file-size { font-size:.78rem; color:var(--ts); font-family:var(--mono); }
        .file-clear { background:none; border:none; color:var(--ts); cursor:pointer; font-size:1.1rem; transition:color .15s; padding:.2rem; }
        .file-clear:hover { color:var(--err); }

        .upload-status { display:none; padding:.65rem .9rem; border-radius:9px; font-size:.85rem; font-family:var(--mono); }
        .upload-status.info { display:block; background:rgba(79,142,247,.08); border:1px solid rgba(79,142,247,.2); color:#93c5fd; }
        .upload-status.success { display:block; background:rgba(16,185,129,.08); border:1px solid rgba(16,185,129,.2); color:#6ee7b7; }
        .upload-status.error { display:block; background:rgba(248,113,113,.08); border:1px solid rgba(248,113,113,.2); color:#fca5a5; }

        .modal-actions { display:flex; gap:.75rem; justify-content:flex-end; }
        .btn-secondary {
            padding:.62rem 1.1rem; border-radius:9px; background:rgba(255,255,255,.06);
            border:1px solid var(--border); color:var(--ts); font-family:inherit;
            font-size:.9rem; cursor:pointer; transition:all .18s;
        }
        .btn-secondary:hover { background:rgba(255,255,255,.1); color:var(--tp); }
        .btn-primary {
            padding:.62rem 1.3rem; border-radius:9px;
            background:linear-gradient(135deg,var(--purple),#6366f1);
            border:none; color:#fff; font-family:inherit; font-size:.9rem;
            font-weight:700; cursor:pointer; transition:all .18s;
            box-shadow:0 4px 14px rgba(167,139,250,.25);
        }
        .btn-primary:hover { filter:brightness(1.1); transform:translateY(-1px); }
        .btn-primary:disabled { background:#2a3347; cursor:not-allowed; transform:none; filter:none; box-shadow:none; }

        /* ── Animations ───────────────────── */
        @keyframes spin { to { transform:rotate(360deg); } }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
        @keyframes fadeIn { from{opacity:0;transform:translateY(-10px)} to{opacity:1;transform:translateY(0)} }
        @keyframes slideUp { from{opacity:0;transform:translateY(14px)} to{opacity:1;transform:translateY(0)} }

        :root {
            color-scheme: light;
            --brand-navy:#16324f;
            --brand-blue:#245fb5;
            --brand-teal:#0f766e;
            --brand-gold:#b7791f;
            --bg:#f5f7fa;
            --card:#ffffff;
            --border:#dce3ec;
            --border-a:#a9bfdf;
            --tp:#172b45;
            --ts:#52647a;
            --tm:#75849a;
            --acc:var(--brand-blue);
            --acc-d:#194b91;
            --acc-g:rgba(36,95,181,.16);
            --ok:var(--brand-teal);
            --err:#b33d42;
            --warn:var(--brand-gold);
            --purple:#6252a3;
            --pill:#edf3fb;
        }
        body {
            align-items:stretch;
            padding:0 0 3rem;
            background:var(--bg);
            background-image:radial-gradient(ellipse 75% 38% at 50% 0%,rgba(49,95,186,.07),transparent 72%);
        }
        body.theme-light { color-scheme:light; }
        body.theme-dark {
            color-scheme:dark;
            --bg:#101b2b;
            --card:#17263a;
            --border:#2b3d54;
            --border-a:#52729c;
            --tp:#eef3f9;
            --ts:#a9b8cb;
            --tm:#8193aa;
            --acc:#79a9f5;
            --acc-d:#407bd0;
            --acc-g:rgba(73,132,215,.24);
            --ok:#50c4a4;
            --err:#ff9292;
            --warn:#f0c16d;
            --purple:#b7a9eb;
            --pill:rgba(73,132,215,.16);
            background:var(--bg);
            background-image:radial-gradient(ellipse 75% 38% at 50% 0%,rgba(36,95,181,.2),transparent 72%);
        }
        body.theme-dark .theme-light { color-scheme:dark; }
        .wrap { max-width:1440px; gap:1.25rem; padding:1.5rem clamp(1rem,3vw,2.5rem) 0; margin:0 auto; }
        header {
            display:flex; align-items:center; gap:1rem;
            text-align:left; padding:.25rem 0 .45rem;
            animation:none;
        }
        .brand-mark {
            display:grid; place-items:center; width:42px; height:42px; flex:none;
            border-radius:12px; background:var(--brand-navy); color:#fff;
            font-size:.92rem; font-weight:800; letter-spacing:-.06em;
            box-shadow:0 5px 12px rgba(22,50,79,.2);
        }
        .brand-copy { min-width:0; }
        .eyebrow { color:var(--acc); font-size:.65rem; letter-spacing:.11em; margin:0 0 .12rem; }
        h1 {
            background:none; color:var(--tp); -webkit-text-fill-color:var(--tp);
            font-size:1.35rem; font-weight:700; letter-spacing:-.035em;
            line-height:1.25; margin:0;
        }
        header p { color:var(--ts); font-size:.82rem; margin-top:.16rem; }
        .header-spacer { flex:1; }
        .header-note { color:var(--ts); font-size:.78rem; }
        .schema-bar {
            position:sticky; top:.5rem; z-index:20;
            background:var(--card); border:1px solid var(--border);
            border-radius:12px; padding:.7rem .85rem;
            gap:.65rem; box-shadow:0 4px 14px rgba(25,39,62,.045);
            backdrop-filter:blur(16px);
        }
        .bar-label { font-size:.67rem; letter-spacing:.08em; }
        kbd { display:inline-block; padding:.08rem .3rem; margin-left:.15rem; border:1px solid var(--border); border-radius:4px; background:var(--bg); color:var(--tm); font-size:.65rem; }
        .schema-tabs { gap:.35rem; }
        .stab { padding:.38rem .68rem; border-radius:7px; font-size:.8rem; }
        .stab:hover { color:var(--acc); }
        .stab.active { background:var(--pill); color:var(--acc); border-color:var(--border-a); box-shadow:none; }
        .stab.custom.active { color:var(--purple); }
        .stab:hover { color:var(--acc); }
        .theme-dark .stab.active { color:#a8c8ff; }
        .upload-btn, .theme-btn {
            margin:0; border-radius:7px; border:1px solid var(--border);
            background:var(--card); color:var(--ts); font-size:.78rem;
            padding:.4rem .62rem; box-shadow:none;
        }
        .upload-btn { color:var(--acc); border-color:var(--border-a); }
        .upload-btn:hover { background:var(--pill); box-shadow:none; }
        .status-dot { font-size:.7rem; margin-left:.2rem; }
        .grid { grid-template-columns:minmax(255px, .75fr) minmax(0, 2fr); gap:1rem; }
        .card {
            background:var(--card); border:1px solid var(--border);
            border-radius:12px; padding:1.1rem;
            box-shadow:0 3px 12px rgba(25,39,62,.035);
            backdrop-filter:none;
        }
        .schema-panel { position:sticky; top:5.1rem; max-height:calc(100vh - 6rem); gap:.7rem; }
        .panel-hdr { color:var(--tp); font-size:.82rem; letter-spacing:0; text-transform:none; padding-bottom:.72rem; }
        .db-badge { background:var(--pill); color:var(--acc); border-color:var(--border); }
        .tbl-block { background:transparent; border:1px solid var(--border); padding:.75rem; border-radius:9px; }
        .tbl-block:hover { background:color-mix(in srgb,var(--pill) 40%,transparent); border-color:var(--border-a); }
        .tbl-name { color:var(--brand-navy); font-size:.8rem; margin-bottom:.45rem; }
        .theme-dark .tbl-name { color:#b8d1f6; }
        .tbl-name::before { color:var(--acc); }
        .col-list { font-size:.7rem; gap:.2rem; }
        .query-panel { gap:1rem; }
        .slabel { color:var(--tp); font-size:.8rem; letter-spacing:0; text-transform:none; font-weight:650; margin-bottom:.45rem; }
        #prompt {
            min-height:54px; padding:.9rem 5.2rem .9rem .95rem;
            border:1px solid var(--border); border-radius:9px;
            background:var(--bg); box-shadow:none; font-size:.94rem;
        }
        #prompt:focus { border-color:var(--acc); box-shadow:0 0 0 3px var(--acc-g); }
        .query-tools { right:.55rem; }
        .query-tools button { width:32px; height:32px; border:1px solid var(--border); border-radius:7px; background:var(--card); }
        #speechLanguage { margin-left:.3rem; padding:.3rem .5rem; font-size:.75rem; }
        .suggestion-group { margin-top:.65rem; }
        .suggestion-title { color:var(--ts); font-size:.64rem; letter-spacing:.04em; }
        .sug-btn { border-color:var(--border); background:var(--bg); color:var(--ts); border-radius:999px; padding:.38rem .68rem; }
        .sug-btn:hover { color:var(--acc); background:var(--pill); border-color:var(--border-a); box-shadow:none; transform:none; }
        #runBtn {
            min-height:44px; align-self:flex-start; padding:.7rem 1rem;
            border-radius:8px; background:var(--acc); box-shadow:none;
            font-size:.84rem; letter-spacing:0;
        }
        #runBtn:hover { background:var(--acc-d); box-shadow:0 4px 10px var(--acc-g); transform:translateY(-1px); }
        .results { gap:.85rem; border-top:1px solid var(--border); padding-top:1rem; }
        .sql-box::before { content:none; }
        .result-summary { padding:.75rem .85rem; color:var(--tp); }
        .result-summary { border-left-color:var(--brand-teal); }
        .stats { gap:.7rem; width:auto; padding:.5rem .65rem; border:0; background:var(--bg); }
        .stats span { color:var(--tp); }
        .tool-btn, .table-control, .chart-select {
            border-color:var(--border); border-radius:7px; background:var(--card);
            color:var(--ts); font-size:.76rem; padding:.4rem .58rem;
        }
        .tool-btn:hover { color:var(--acc); background:var(--pill); }
        .result-toolbar { gap:.45rem; }
        .chart-panel { background:var(--card); border-color:var(--border); }
        .chart-mark { fill:var(--brand-blue); }
        .chart-line { stroke:var(--brand-blue); }
        .geo-mark { fill:#c77422; }
        .ok, .status-dot { color:var(--brand-teal); }
        .status-dot::before { background:var(--brand-teal); box-shadow:0 0 6px var(--brand-teal); }
        .status-dot.busy { color:var(--warn); }
        .status-dot.busy::before { background:var(--warn); box-shadow:0 0 6px var(--warn); }
        .tbl-wrap { border-radius:8px; border-color:var(--border); }
        table { font-size:.81rem; }
        th { background:var(--bg); color:var(--ts); font-size:.68rem; }
        td { border-color:var(--border); padding:.58rem .75rem; }
        tr:hover td { background:var(--pill); }
        .theme-light .sql-box { background:var(--bg); border-color:var(--border); }
        .theme-light .sql-box::before { background:var(--bg); }
        .theme-light th { background:var(--bg); }
        .theme-light .tbl-block { background:transparent; }
        .theme-dark .sql-box { background:#101927; border-color:var(--border); }
        .theme-dark .sql-box::before { background:#101927; }
        .theme-dark .sql-highlight { color:#7de0be; }
        .theme-dark .sql-keyword { color:#93c5fd; }
        .theme-dark .sql-string { color:#fbbf24; }
        .theme-dark th { background:#121c2a; }
        .theme-dark .tbl-block { background:transparent; border-color:var(--border); }
        .theme-dark .modal { background:var(--card); border-color:var(--border); }
        .theme-dark .drop-zone { background:var(--bg); border-color:var(--border-a); }
        .theme-dark .upload-btn { color:#b8d1f6; }
        .theme-dark .result-summary { border-left-color:var(--ok); }
        .theme-dark .chart-mark { fill:#79a9f5; }
        .theme-dark .chart-line { stroke:#79a9f5; }
        .grid + .grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
        .schema-tools { padding:.85rem 1rem; }
        .schema-tools .tool-btn { background:var(--card); }
        .command-box { background:var(--card); }
        .modal {
            background:var(--card); border-color:var(--border);
            border-radius:14px; padding:1.35rem; gap:1rem;
            box-shadow:0 24px 70px rgba(19,31,49,.2);
        }
        .modal-hdr h2 { background:none; color:var(--tp); -webkit-text-fill-color:var(--tp); font-size:1.12rem; }
        .type-chip { background:var(--bg); }
        :focus-visible { outline:3px solid var(--acc-g); outline-offset:2px; }
        button, input, select, textarea { font-family:inherit; }
        @media (max-width: 860px) {
            body { padding:0 0 2rem; }
            .wrap { padding:1rem 1rem 0; gap:.85rem; }
            header { gap:.7rem; }
            .schema-panel { position:static; max-height:420px; }
            .grid { grid-template-columns:minmax(0,1fr); }
        }
        @media (max-width: 600px) {
            .wrap { padding:.8rem .7rem 0; }
            header { flex-wrap:wrap; }
            .brand-mark { width:38px; height:38px; }
            .header-note { display:none; }
            .schema-bar { position:static; }
            .schema-tabs { flex-basis:100%; order:2; }
            .schema-bar .upload-btn { margin-left:0; }
            .schema-bar .status-dot { margin-left:auto; }
            .grid + .grid { grid-template-columns:1fr; }
            .card { padding:.9rem; }
            .result-toolbar { justify-content:flex-start; }
            .result-toolbar > * { flex:0 1 auto; }
            #runBtn { width:100%; }
        }

        /* Pastel workspace palette */
        :root {
            color-scheme:light;
            --brand-cream:#EDE8D0;
            --brand-mint:#D0EDDA;
            --brand-periwinkle:#D0D5ED;
            --brand-blush:#EDD0E4;
            --brand-ink:#343344;
            --brand-muted:#625f69;
            --brand-navy:#343344;
            --brand-blue:#565d80;
            --brand-teal:#496f5b;
            --brand-gold:#80623d;
            --bg:var(--brand-cream);
            --card:#fffdf6;
            --border:#d8d2bb;
            --border-a:#a7aac2;
            --tp:var(--brand-ink);
            --ts:var(--brand-muted);
            --tm:#77727b;
            --acc:var(--brand-blue);
            --acc-d:#3b405f;
            --acc-g:rgba(86,93,128,.2);
            --ok:var(--brand-teal);
            --err:#9f4652;
            --warn:var(--brand-gold);
            --purple:#68516c;
            --pill:var(--brand-periwinkle);
        }
        body {
            color:var(--tp);
            background:var(--brand-cream);
            background-image:radial-gradient(ellipse 75% 38% at 50% 0%,rgba(208,213,237,.56),transparent 72%);
        }
        body.theme-dark {
            color-scheme:dark;
            --bg:#282735;
            --card:#353343;
            --border:#514f60;
            --border-a:#9b9dbb;
            --tp:#f4f0e2;
            --ts:#d0ccbf;
            --tm:#aaa6a0;
            --acc:#c6cae4;
            --acc-d:#aeb4d8;
            --acc-g:rgba(208,213,237,.22);
            --ok:#b9e2c6;
            --err:#f2b9cb;
            --warn:#eddb9c;
            --purple:#edc8e0;
            --pill:rgba(208,213,237,.18);
            background:var(--bg);
            background-image:radial-gradient(ellipse 75% 38% at 50% 0%,rgba(208,213,237,.16),transparent 72%);
        }
        .wrap { max-width:1440px; gap:1.15rem; }
        header { padding:.25rem 0 .45rem; }
        .brand-mark {
            background:var(--brand-periwinkle);
            color:var(--brand-ink);
            border:1px solid #b9bfdd;
            box-shadow:0 5px 12px rgba(77,71,91,.12);
        }
        .eyebrow { color:#565d80; }
        .header-note { padding:.42rem .7rem; border:1px solid #bfd9c6; border-radius:999px; background:var(--brand-mint); color:#395345; }
        .schema-bar {
            display:grid; grid-template-columns:1fr; gap:.65rem; position:sticky; top:.5rem; z-index:20;
            padding:.9rem 1rem; border:1px solid #d8d2bb;
            border-radius:15px; background:var(--brand-cream);
            box-shadow:0 5px 18px rgba(63,57,72,.06); backdrop-filter:blur(16px);
        }
        .workspace-overview { display:flex; align-items:center; justify-content:space-between; gap:.75rem; }
        .workspace-title { display:flex; align-items:baseline; gap:.55rem; }
        .bar-label { color:var(--brand-muted); font-weight:700; letter-spacing:.08em; }
        .workspace-title strong { color:var(--brand-ink); font-size:.86rem; }
        .workspace-actions { display:flex; align-items:center; gap:.5rem; flex-wrap:wrap; }
        .schema-tabs { flex:1 1 280px; gap:.4rem; }
        .stab { padding:.43rem .75rem; border-radius:9px; font-size:.8rem; background:rgba(255,255,255,.52); }
        .stab:hover { color:var(--brand-ink); background:var(--brand-mint); border-color:#a9cdb5; }
        .stab.active { background:var(--brand-periwinkle); color:var(--brand-ink); border-color:#b1b8d7; box-shadow:none; }
        .stab.custom.active { color:#504052; background:var(--brand-blush); border-color:#d8b8cf; }
        .upload-btn, .theme-btn {
            margin:0; border-radius:9px; border:1px solid #c5bdb0;
            background:#fffdf6; color:var(--brand-ink); font-size:.78rem;
            padding:.45rem .68rem; box-shadow:none;
        }
        .upload-btn:hover, .theme-btn:hover { background:var(--brand-blush); box-shadow:none; }
        .status-dot { margin-left:auto; padding:.4rem .1rem; color:#496f5b; }
        .grid { grid-template-columns:minmax(260px,.76fr) minmax(0,2fr); gap:1.15rem; }
        .card {
            background:var(--card); border:1px solid rgba(69,63,76,.1);
            border-radius:16px; padding:1.2rem;
            box-shadow:0 8px 26px rgba(63,57,72,.055); backdrop-filter:none;
        }
        .schema-panel { position:sticky; top:5.1rem; max-height:calc(100vh - 6rem); gap:.7rem; background:var(--brand-mint); border-color:#bfdfc9; }
        .panel-hdr { color:var(--tp); font-size:.84rem; letter-spacing:0; text-transform:none; padding-bottom:.72rem; border-color:rgba(52,51,68,.14); }
        .db-badge { background:rgba(255,255,255,.58); color:var(--brand-ink); border-color:rgba(52,51,68,.12); }
        .tbl-block { background:rgba(255,255,255,.52); border:1px solid rgba(52,51,68,.1); padding:.78rem; border-radius:11px; }
        .tbl-block:hover { background:rgba(255,255,255,.8); border-color:#a9cdb5; }
        .tbl-name { color:var(--brand-ink); font-size:.8rem; margin-bottom:.45rem; }
        .query-panel { gap:1rem; }
        .slabel { color:var(--tp); font-size:.8rem; letter-spacing:0; text-transform:none; font-weight:650; margin-bottom:.45rem; }
        #prompt { min-height:54px; padding:.9rem 5.2rem .9rem .95rem; border:1px solid #d8d2bb; border-radius:11px; background:#fffdf6; box-shadow:none; font-size:.94rem; color:var(--tp); }
        #prompt:focus { border-color:#898fac; box-shadow:0 0 0 3px var(--acc-g); }
        .query-tools button { width:32px; height:32px; border:1px solid #d8d2bb; border-radius:8px; background:var(--brand-cream); }
        #voiceBtn.voice-recording { background:var(--brand-blush); border-color:#d9bad0; animation:pulse 1.2s ease-in-out infinite; }
        .suggestion-group { margin-top:.65rem; }
        .suggestion-title { color:var(--ts); font-size:.64rem; letter-spacing:.04em; }
        .sug-btn { border-color:#c2c4dc; background:var(--brand-periwinkle); color:var(--brand-ink); border-radius:999px; padding:.4rem .72rem; }
        .suggestion-group:nth-child(2) .sug-btn { border-color:#d9bad0; background:var(--brand-blush); }
        .suggestion-group:nth-child(3) .sug-btn { border-color:#b9d9c3; background:var(--brand-mint); }
        .sug-btn:hover { color:var(--brand-ink); background:#fff; border-color:var(--brand-ink); box-shadow:none; transform:translateY(-1px); }
        #runBtn { min-height:44px; align-self:flex-start; padding:.7rem 1rem; border-radius:10px; background:var(--brand-periwinkle); color:var(--brand-ink); box-shadow:none; font-size:.84rem; letter-spacing:0; }
        #runBtn:hover { background:#bdc5e5; box-shadow:0 4px 10px var(--acc-g); transform:translateY(-1px); }
        #runBtn:disabled { background:#d2d3dc; color:#6b6a73; }
        .results { gap:.85rem; border-top:1px solid var(--border); padding-top:1rem; }
        .result-summary { padding:.85rem 1rem; color:var(--tp); border:1px solid #bfdfc9; border-left:4px solid #9bc9a9; background:var(--brand-mint); border-radius:11px; }
        .stats { gap:.7rem; width:auto; padding:.55rem .7rem; border:0; background:var(--brand-cream); border-radius:9px; }
        .stats span { color:var(--tp); }
        .tool-btn, .table-control, .chart-select { border-color:var(--border); border-radius:8px; background:var(--card); color:var(--ts); font-size:.76rem; padding:.4rem .58rem; }
        .tool-btn:hover { color:var(--brand-ink); background:var(--brand-periwinkle); }
        .result-toolbar { gap:.45rem; }
        .chart-panel { background:var(--brand-periwinkle); border-color:#bdc2dd; border-radius:13px; }
        .chart-mark { fill:#6c7293; }
        .chart-line { stroke:#656b8b; }
        .geo-mark { fill:#76586f; }
        .chart-grid { stroke:rgba(52,51,68,.2); }
        .chart-label { fill:var(--brand-ink); }
        .geo-bg { fill:rgba(255,255,255,.38); stroke:var(--border-a); }
        .status-dot::before { background:var(--brand-teal); box-shadow:0 0 6px var(--brand-teal); }
        .status-dot.busy { color:var(--warn); }
        .tbl-wrap { border-radius:9px; border-color:var(--border); }
        table { font-size:.81rem; }
        th { background:#e4e6f2; color:#45465c; font-size:.68rem; }
        td { border-color:var(--border); padding:.58rem .75rem; }
        tr:hover td { background:rgba(208,213,237,.42); }
        .sql-box, .theme-light .sql-box { background:#fffdf6; border-color:var(--border); }
        .theme-light .sql-box::before { background:#fffdf6; }
        .theme-light th { background:#e4e6f2; }
        .theme-dark .sql-box { background:#292835; border-color:var(--border); }
        .theme-dark .sql-box::before { background:#292835; }
        .theme-dark .sql-highlight { color:#b9e2c6; }
        .theme-dark .sql-keyword { color:#c6cae4; }
        .theme-dark .sql-string { color:#eddb9c; }
        .theme-dark th { background:#434153; }
        .theme-dark .tbl-block { background:rgba(255,255,255,.04); border-color:var(--border); }
        .theme-dark .modal { background:var(--card); border-color:var(--border); }
        .theme-dark .drop-zone { background:var(--bg); border-color:var(--border-a); }
        .theme-dark .upload-btn { color:var(--tp); }
        .theme-dark .schema-bar { background:var(--card); border-color:var(--border); }
        .theme-dark .schema-panel { background:rgba(208,237,218,.1); border-color:rgba(208,237,218,.25); }
        .theme-dark .brand-mark { background:rgba(208,213,237,.18); border-color:var(--border-a); color:var(--tp); }
        .theme-dark .header-note { background:rgba(208,237,218,.13); border-color:rgba(208,237,218,.25); color:var(--tp); }
        .theme-dark .workspace-title strong { color:var(--tp); }
        .theme-dark .result-summary { border-color:rgba(208,237,218,.35); border-left-color:var(--ok); background:rgba(208,237,218,.12); }
        .theme-dark .chart-panel { background:rgba(208,213,237,.12); }
        .theme-dark .chart-mark { fill:#c6cae4; }
        .theme-dark .chart-line { stroke:#c6cae4; }
        .theme-dark .chart-label { fill:var(--tp); }
        .theme-dark .geo-bg { fill:rgba(208,213,237,.08); }
        .theme-dark .stats { background:rgba(237,232,208,.1); }
        .theme-dark #prompt, .theme-dark .tool-btn, .theme-dark .table-control, .theme-dark .chart-select { background:var(--card); }
        .theme-dark .stab.active { color:var(--tp); }
        .theme-dark .suggestion-group .sug-btn { color:var(--tp); border-color:var(--border-a); background:rgba(208,213,237,.16); }
        .theme-dark .suggestion-group:nth-child(2) .sug-btn { background:rgba(237,208,228,.14); }
        .theme-dark .suggestion-group:nth-child(3) .sug-btn { background:rgba(208,237,218,.14); }
        .schema-tools { padding:.85rem 1rem; }
        .schema-tools .tool-btn { background:var(--card); }
        .command-box { background:var(--card); }
        .modal { background:var(--card); border-color:var(--border); border-radius:14px; padding:1.35rem; gap:1rem; box-shadow:0 24px 70px rgba(19,31,49,.16); }
        .modal-hdr h2 { background:none; color:var(--tp); -webkit-text-fill-color:var(--tp); font-size:1.12rem; }
        .type-chip { background:var(--bg); }
        :focus-visible { outline:3px solid var(--acc-g); outline-offset:2px; }
        @media (max-width:860px) {
            .schema-panel { position:static; max-height:420px; }
            .grid { grid-template-columns:minmax(0,1fr); }
        }
        @media (max-width:600px) {
            .wrap { padding:.8rem .7rem 0; }
            .schema-bar { position:static; }
            .workspace-actions { align-items:stretch; }
            .workspace-actions .schema-tabs { flex-basis:100%; }
            .workspace-actions .upload-btn { margin-left:0; }
            .workspace-overview { align-items:flex-start; }
            .schema-bar .status-dot { margin-left:auto; }
            .grid + .grid { grid-template-columns:1fr; }
            .card { padding:.9rem; }
            .result-toolbar { justify-content:flex-start; }
            .result-toolbar > * { flex:0 1 auto; }
            #runBtn { width:100%; }
        }
    </style>
</head>
<body>
<div class="wrap">
    <header>
        <div class="brand-mark" aria-hidden="true">NS</div>
        <div class="brand-copy">
            <div class="eyebrow">NATURAL LANGUAGE ANALYTICS</div>
            <h1>NLPSQL Studio</h1>
            <p>Explore your data with questions, not query syntax.</p>
        </div>
        <div class="header-spacer"></div>
        <span class="header-note">Private, local-first workspace</span>
    </header>

    <!-- Schema / Tab Bar -->
    <div class="schema-bar" id="schemaBar">
        <div class="workspace-overview">
            <div class="workspace-title"><span class="bar-label">WORKSPACE</span><strong>Choose a dataset</strong></div>
            <span class="status-dot" id="statusDot" role="status">Connected</span>
        </div>
        <div class="workspace-actions">
            <div class="schema-tabs" id="schemaTabs"></div>
            <button class="upload-btn" type="button" onclick="openUpload()">Upload data</button>
            <button class="theme-btn" id="themeBtn" type="button" onclick="toggleTheme()">Switch theme</button>
            <button class="theme-btn" type="button" onclick="openCommandPalette()">Commands <kbd>Ctrl K</kbd></button>
            <button class="theme-btn" type="button" onclick="connectDatabase()">Connect database</button>
        </div>
    </div>

    <div class="grid">
        <!-- Schema Explorer -->
        <div class="card schema-panel" id="schemaPanel">
            <div class="panel-hdr">
                <span>Data structure</span>
                <span class="db-badge" id="dbBadge">SQLite</span>
            </div>
            <div id="schemaContent"><div class="loading-msg">Loading…</div></div>
        </div>

        <!-- Query + Results -->
        <div class="card query-panel">
            <div>
                <div class="slabel">Ask your data a question</div>
                <div class="inp-wrap">
                    <input type="text" id="prompt" autocomplete="off"
                        list="queryHints" aria-label="Ask your data a question" placeholder="e.g. Which departments have the highest average salary?">
                    <datalist id="queryHints"></datalist>
                    <div class="query-tools"><button type="button" id="voiceBtn" onclick="startVoiceInput()" title="Voice input; local Whisper is used if browser recognition cannot connect" aria-label="Start voice input">🎙</button><button type="button" onclick="speakSummary()" title="Read answer aloud">🔊</button></div>
                </div>
                <label class="chart-heading" for="speechLanguage">Voice language
                    <select id="speechLanguage" class="chart-select">
                        <option value="en-IN">English (India)</option>
                        <option value="hi-IN">Hindi</option>
                        <option value="kn-IN">Kannada</option>
                    </select>
                </label>
            </div>

            <div>
                <div class="slabel">Start with an example</div>
                <div class="suggestions" id="suggestions" style="margin-top:.45rem;"></div>
            </div>

            <button id="runBtn" onclick="runQuery()">
                <span id="btnTxt">Generate &amp; Execute SQL</span>
                <div class="spin" id="spin"></div>
            </button>

            <div class="results" id="results">
                <div id="transBanner" style="display:none;background:rgba(99,157,255,0.1);border:1px solid rgba(99,157,255,0.3);border-radius:8px;padding:.5rem .8rem;font-size:.85rem;color:#93c5fd;margin-bottom:.5rem;"></div>
                <div class="stats">
                    ⚡ LLM: <span id="tInf">-</span>
                    &nbsp;🗄 Query: <span id="tQ">-</span>
                    &nbsp;🔁 Attempts: <span id="tAtt">-</span>
                </div>
                <div class="result-summary" id="summaryOut" aria-live="polite"></div>
                <div class="result-toolbar" id="resultToolbar">
                    <div class="chart-heading">
                        <label for="chartType">Visualization</label>
                        <select class="chart-select" id="chartType" onchange="renderChart()">
                            <option value="auto">Recommended</option>
                            <option value="bar">Bar</option>
                            <option value="line">Line</option>
                            <option value="pie">Pie</option>
                            <option value="scatter">Scatter</option>
                            <option value="map">Geo map</option>
                        </select>
                    </div>
                    <div class="result-toolbar">
                        <button class="tool-btn" type="button" onclick="exportResults('csv')">Export CSV</button>
                            <button class="tool-btn" type="button" onclick="exportResults('excel')">Export Excel</button>
                            <button class="tool-btn" type="button" onclick="exportResults('json')">Export JSON</button>
                            <button class="tool-btn" type="button" onclick="printReport()">PDF report</button>
                            <button class="tool-btn" type="button" onclick="pinResults()">Pin</button>
                            <button class="tool-btn" type="button" onclick="shareQuery()">Share</button>
                            <input class="table-control" id="chartEdit" aria-label="Describe chart changes" placeholder="e.g. line chart, sort descending" onkeydown="if(event.key==='Enter')handleChartEdit(event)">
                            <button class="tool-btn" type="button" onclick="handleChartEdit({currentTarget:document.getElementById('chartEdit')})">Apply chart edit</button>
                        </div>
                    </div>
                    <div class="progress-msg" id="progressMsg" role="status" aria-live="polite"></div>
                    <div class="diff-panel" id="diffPanel"></div>
                    <div class="sql-box"><label for="sqlOut" class="slabel">Editable SQL (read-only validation applies)</label><div class="sql-editor-wrap"><pre id="sqlHighlight" class="sql-highlight" aria-hidden="true"></pre><textarea id="sqlOut" class="sql-editor" spellcheck="false" oninput="updateSqlHighlight()" onscroll="syncSqlHighlight()"></textarea></div><div class="sql-actions"><button class="tool-btn" type="button" onclick="runEditedSql()">Execute edited SQL</button></div></div>
                    <div class="followups" id="followups"></div>
                    <div class="chart-panel" id="chartPanel">
                    <div class="chart-canvas" id="chartOut"></div>
                </div>
                <div id="tableOut"></div>
            </div>
        </div>
    </div>
    <div class="grid">
        <div class="card history-panel"><div class="panel-hdr">Query history <button class="tool-btn" type="button" onclick="clearHistory()">Clear</button></div><div id="historyOut"></div><div class="panel-hdr">Favorites</div><div id="favoritesOut"></div></div>
        <div class="card pin-panel"><div class="panel-hdr">Pinned dashboard</div><div id="pinsOut"></div></div>
    </div>
    <div class="card schema-tools">
        <button class="tool-btn" type="button" onclick="showErDiagram()">Interactive ER diagram</button>
        <div class="er-canvas" id="erCanvas" hidden></div>
    </div>
    <div class="card" id="samplePanel" hidden></div>
</div>

<div class="command-overlay" id="commandOverlay" onclick="if(event.target===this)closeCommandPalette()">
    <div class="command-box">
        <input id="commandSearch" type="search" placeholder="Search commands…" oninput="filterCommands(this.value)">
        <div class="command-list">
            <button type="button" onclick="closeCommandPalette();document.getElementById('prompt').focus()">Focus query input</button>
            <button type="button" onclick="closeCommandPalette();runQuery()">Run current query</button>
            <button type="button" onclick="closeCommandPalette();openUpload()">Upload dataset</button>
            <button type="button" onclick="closeCommandPalette();toggleTheme()">Toggle theme</button>
            <button type="button" onclick="closeCommandPalette();showErDiagram()">Open ER diagram</button>
            <button type="button" onclick="closeCommandPalette();connectDatabase()">Connect local database</button>
            <button type="button" onclick="closeCommandPalette();startVoiceInput()">Start voice input</button>
        </div>
    </div>
</div>

<!-- Upload Modal -->
<div class="overlay" id="overlay" onclick="overlayClick(event)">
    <div class="modal" id="modal">
        <div class="modal-hdr">
            <h2>Upload Custom Schema</h2>
            <button class="close-btn" onclick="closeUpload()">✕</button>
        </div>

        <p style="color:var(--ts);font-size:.9rem;line-height:1.55;">
            Upload any of the supported formats below. The schema will be loaded into an
            in-memory SQLite database and added as a new tab you can query instantly.
        </p>

        <!-- Drop Zone -->
        <div class="drop-zone" id="dropZone"
             ondragover="onDragOver(event)" ondragleave="onDragLeave(event)" ondrop="onDrop(event)">
            <input type="file" id="fileInput" accept=".sql,.csv,.json,.pdf,.png,.jpg,.jpeg" multiple
                   onchange="onFileSelect(event)">
            <div class="drop-icon">📂</div>
            <div class="drop-title">Drop file here or click to browse</div>
            <div class="drop-sub">Drag &amp; drop your schema file</div>
            <div class="drop-types">
                <span class="type-chip">.sql</span>
                <span class="type-chip">.csv</span>
                <span class="type-chip">.json</span>
                <span class="type-chip">.pdf</span>
                <span class="type-chip">multiple CSV/JSON</span>
                <span class="type-chip">.png / .jpg OCR</span>
            </div>
        </div>

        <!-- File Preview -->
        <div class="file-preview" id="filePreview">
            <span class="file-icon" id="fileIcon">📄</span>
            <div class="file-info">
                <div class="file-name" id="fileName">—</div>
                <div class="file-size" id="fileSize">—</div>
            </div>
            <button class="file-clear" onclick="clearFile()" title="Remove file">✕</button>
        </div>

        <!-- Status -->
        <div class="upload-status" id="uploadStatus"></div>
        <div class="upload-preview" id="uploadPreview"></div>

        <div class="modal-actions">
            <button class="btn-secondary" onclick="closeUpload()">Cancel</button>
            <button class="btn-primary" id="uploadBtn" onclick="doUpload()" disabled>
                Import Schema
            </button>
        </div>
    </div>
</div>

<script>
// ── State ─────────────────────────────────────────────────────────────────────
let curSchema   = '__SCHEMA__';
let allSchemas  = [];
let uploadedSet = new Set();   // display names of custom uploaded schemas
let pendingFile = null;        // File object awaiting upload
let resultState = null;
let voiceRecognition = null;
let voiceRecorder = null;
let voiceStream = null;
let voiceChunks = [];
let preferLocalVoice = false;
let voiceStarting = false;
let voiceStopTimer = null;
let resultPage = 0;
let resultPageSize = 10;
let resultFilter = '';
let resultSort = {column: -1, direction: 1};
let recentQueries = [];
let favoriteQueries = [];
let pinnedResults = [];
let currentSchemaTables = [];

const SUGGESTIONS = __SUGGESTIONS__;

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
    applyTheme(localStorage.getItem('nlpsql-theme-v2') || 'light');
    recentQueries = loadStoredList('nlpsql-history');
    favoriteQueries = loadStoredList('nlpsql-favorites');
    pinnedResults = loadStoredList('nlpsql-pins');
    renderSavedQueries();
    renderPins();
    const d = await fetch('/api/schemas').then(r => r.json());
    allSchemas  = d.available_schemas || [];
    curSchema   = d.current_schema   || 'company';
    uploadedSet = new Set(d.uploaded_schemas || []);
    renderTabs();
    renderSuggestions(curSchema);
    await refreshSchemaPanel();
    const shared = new URLSearchParams(location.hash.slice(1));
    const sharedQuestion = shared.get('q');
    if (sharedQuestion) {
        const sharedSchema = shared.get('schema');
        if (sharedSchema && sharedSchema !== curSchema && allSchemas.includes(sharedSchema)) await switchSchema(sharedSchema);
        document.getElementById('prompt').value = sharedQuestion;
        runQuery();
    }
}

// ── Tabs ──────────────────────────────────────────────────────────────────────
function renderTabs() {
    const el = document.getElementById('schemaTabs');
    el.innerHTML = allSchemas.map(s => {
        const isCustom = uploadedSet.has(s);
        return `<button class="stab${s===curSchema?' active':''} ${isCustom?'custom':''}"
                    id="tab-${CSS.escape(s)}" onclick="switchSchema('${s}')">${s}</button>`;
    }).join('');
}

async function switchSchema(name) {
    if (name === curSchema) return;
    setStatus('busy', 'Switching…');
    document.getElementById('schemaContent').innerHTML = '<div class="loading-msg">Switching…</div>';
    document.querySelectorAll('.stab').forEach(t => t.classList.remove('active'));
    document.getElementById('tab-' + CSS.escape(name))?.classList.add('active');

    const d = await fetch('/api/switch_schema', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({schema: name})
    }).then(r => r.json());

    if (d.status === 'ok') {
        curSchema = name;
        renderSuggestions(name);
        await refreshSchemaPanel();
        document.getElementById('results').style.display = 'none';
        document.getElementById('prompt').value = '';
        setStatus('ok', 'Connected');
    } else {
        setStatus('ok', 'Error');
    }
}

// ── Schema Panel ──────────────────────────────────────────────────────────────
async function refreshSchemaPanel() {
    const d = await fetch('/api/schema_detail').then(r => r.json());
    document.getElementById('dbBadge').textContent = d.engine || 'SQLite';
    const tables = d.tables || [];
    currentSchemaTables = tables;
    renderAutocomplete();
    if (!tables.length) {
        document.getElementById('schemaContent').innerHTML = '<div class="loading-msg">No tables found.</div>';
        return;
    }
    document.getElementById('schemaContent').innerHTML = tables.map(t => `
        <div class="tbl-block">
            <div class="tbl-name" role="button" tabindex="0" onclick="previewTable(decodeURIComponent('${encodeURIComponent(t.name).replace(/'/g, '%27')}'))" onkeydown="if(event.key==='Enter')previewTable(decodeURIComponent('${encodeURIComponent(t.name).replace(/'/g, '%27')}'))">${esc(t.name)}</div>
            <ul class="col-list">${t.columns.map(c => `
                <li>
                    <span class="cn">${esc(c.name)}</span>
                    <span class="ct">${esc(c.type)}</span>
                    ${c.is_pk ? '<span class="pk">PK</span>' : ''}
                    ${c.is_fk ? '<span class="fk">FK</span>' : ''}
                </li>`).join('')}
            </ul>
        </div>`).join('');
}

// ── Suggestions ───────────────────────────────────────────────────────────────
function renderSuggestions(schema) {
    const list = SUGGESTIONS[schema] || [];
    if (!list.length) {
        document.getElementById('suggestions').innerHTML = '<span style="color:var(--tm);font-size:.82rem;">No suggestions for this schema yet.</span>';
        return;
    }
    const groups = [
        {title:'Simple', values:list.filter((_, index) => index < 2)},
        {title:'Moderate', values:list.filter((_, index) => index >= 2 && index < 4)},
        {title:'Complex', values:list.filter((_, index) => index >= 4)},
    ];
    document.getElementById('suggestions').innerHTML = groups.filter(group => group.values.length).map(group =>
        `<div class="suggestion-group"><span class="suggestion-title">${group.title}</span><div class="suggestions">${group.values.map(s =>
            `<button class="sug-btn" onclick="setPrompt(this.innerText)">${esc(s)}</button>`
        ).join('')}</div></div>`
    ).join('');
}

function setPrompt(t) {
    document.getElementById('prompt').value = t;
    document.getElementById('prompt').focus();
}

// ── Query ─────────────────────────────────────────────────────────────────────
async function runQuery() {
    const q = document.getElementById('prompt').value.trim();
    if (!q) return;
    const btn = document.getElementById('runBtn');
    btn.disabled = true;
    document.getElementById('spin').style.display = 'block';
    document.getElementById('btnTxt').style.display = 'none';

    showProgress('Connecting to query pipeline');
    try {
        const response = await fetch('/api/query_stream', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({prompt: q})
        });
        if (!response.ok || !response.body) throw new Error(`Query request failed (${response.status})`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
            const {value, done} = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, {stream:true});
            const events = buffer.split('\n\n');
            buffer = events.pop();
            events.forEach(event => {
                const line = event.split('\n').find(item => item.startsWith('data: '));
                if (!line) return;
                const message = JSON.parse(line.slice(6));
                if (message.type === 'progress') showProgress(message.message);
                else if (message.type === 'result') renderQueryResult(message.data, q);
                else if (message.type === 'error') throw new Error(message.message);
            });
        }
        hideProgress();
    } catch(e) {
        document.getElementById('results').style.display = 'flex';
        document.getElementById('tableOut').innerHTML = `<div class="err-box"><strong>Network Error:</strong><br>${esc(e.message)}</div>`;
    } finally {
        hideProgress();
        btn.disabled = false;
        document.getElementById('spin').style.display = 'none';
        document.getElementById('btnTxt').style.display = 'block';
    }
}

function renderQueryResult(d, question) {
    document.getElementById('results').style.display = 'flex';
    const banner = document.getElementById('transBanner');
    banner.style.display = d.is_translated && d.translated_query ? 'block' : 'none';
    if (banner.style.display === 'block') banner.innerHTML = `🌐 <strong>Translated to English:</strong> "${esc(d.translated_query)}"`;
    document.getElementById('sqlOut').value = d.sql || '';
    updateSqlHighlight();
    document.getElementById('tInf').textContent = d.inference_time != null ? d.inference_time.toFixed(3)+'s' : '-';
    document.getElementById('tQ').textContent = d.query_time != null ? d.query_time.toFixed(4)+'s' : '-';
    document.getElementById('tAtt').textContent = d.attempts ?? '-';
    const out = document.getElementById('tableOut');
    const diff = document.getElementById('diffPanel');
    if (Array.isArray(d.sql_history) && d.sql_history.length > 1) {
        diff.style.display = 'block';
        diff.innerHTML = `<strong>Repair loop · original and repaired SQL</strong><div class="sql-diff"><div><span style="color:var(--err)">− Original</span><pre>${esc(d.sql_history[0])}</pre></div><div><span style="color:var(--ok)">+ Repaired</span><pre>${esc(d.sql_history[d.sql_history.length - 1])}</pre></div></div>`;
    } else {
        diff.style.display = 'none';
        diff.textContent = '';
    }
    if (d.error) {
        resultState = null;
        document.getElementById('summaryOut').textContent = '';
        document.getElementById('chartOut').innerHTML = '';
        document.getElementById('followups').innerHTML = '';
        document.getElementById('chartPanel').style.display = 'none';
        document.getElementById('resultToolbar').style.display = 'none';
        out.innerHTML = `<div class="err-box"><strong>⚠ Error:</strong><br>${esc(d.error)}</div>`;
        recordQuery(question, false);
        return;
    }
    if (!Array.isArray(d.columns) || !Array.isArray(d.rows)) {
        throw new Error('Query response did not contain result columns and rows.');
    }
    resultState = {question, sql: d.sql || '', columns: d.columns, rows: d.rows, truncated: d.truncated};
    resultPage = 0;
    resultFilter = '';
    resultSort = {column: -1, direction: 1};
    document.getElementById('chartType').value = 'auto';
    document.getElementById('resultToolbar').style.display = 'flex';
    document.getElementById('chartPanel').style.display = d.rows.length ? 'block' : 'none';
    renderSummary();
    applyChartEdit(question);
    renderResultsTable();
    renderChart();
    renderFollowups();
    recordQuery(question, true);
    updateShareLink(question, curSchema);
}

function showProgress(message) {
    const el = document.getElementById('progressMsg');
    el.textContent = `⏳ ${message}`;
    el.classList.add('active');
}

function hideProgress() {
    document.getElementById('progressMsg').classList.remove('active');
}

function loadStoredList(key) {
    try {
        const value = JSON.parse(localStorage.getItem(key) || '[]');
        return Array.isArray(value) ? value : [];
    } catch (error) {
        console.error(`Could not read ${key} from local storage`, error);
        return [];
    }
}

function saveStoredList(key, value) {
    try {
        localStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
        console.error(`Could not save ${key} to local storage`, error);
        setStatus('ok', 'Storage unavailable');
    }
}

function recordQuery(question, successful) {
    recentQueries = [{question, schema:curSchema, successful, at:Date.now()}, ...recentQueries.filter(item => item.question !== question || item.schema !== curSchema)].slice(0, 40);
    saveStoredList('nlpsql-history', recentQueries);
    renderSavedQueries();
}

function renderSavedQueries() {
    const out = document.getElementById('historyOut');
    if (!out) return;
    if (!recentQueries.length) {
        out.innerHTML = '<div class="chart-empty">Your recent questions stay in this browser.</div>';
    } else {
        out.innerHTML = recentQueries.slice(0, 12).map((item, index) => `
            <div class="saved-item"><button type="button" onclick="replayQuery(${index})">${esc(item.question)} <small>· ${esc(item.schema)}</small></button>
            <button type="button" aria-label="Favorite query" onclick="toggleFavorite(${index})">${isFavorite(item) ? '★' : '☆'}</button></div>`).join('');
    }
    const favorites = document.getElementById('favoritesOut');
    favorites.innerHTML = favoriteQueries.length
        ? favoriteQueries.map((item, index) => `<div class="saved-item"><button type="button" onclick="replayFavorite(${index})">${esc(item.question)} <small>· ${esc(item.schema)}</small></button><button type="button" onclick="removeFavorite(${index})" aria-label="Remove favorite">×</button></div>`).join('')
        : '<div class="chart-empty">Favorite a recent query to keep it here.</div>';
}

function renderPins() {
    const out = document.getElementById('pinsOut');
    if (!out) return;
    if (!pinnedResults.length) {
        out.innerHTML = '<div class="chart-empty">Pin a result to keep a local snapshot here.</div>';
        return;
    }
    out.innerHTML = pinnedResults.map((item, index) => `
        <div class="saved-item"><button type="button" onclick="openPin(${index})">${esc(item.question)} <small>· ${esc(item.schema)} · ${item.rows.length} rows</small></button>
        <button type="button" aria-label="Remove pin" onclick="removePin(${index})">×</button></div>`).join('');
}

function isFavorite(item) {
    return favoriteQueries.some(saved => saved.question === item.question && saved.schema === item.schema);
}

function toggleFavorite(index) {
    const item = recentQueries[index];
    if (!item) return;
    favoriteQueries = isFavorite(item)
        ? favoriteQueries.filter(saved => saved.question !== item.question || saved.schema !== item.schema)
        : [{question:item.question, schema:item.schema}, ...favoriteQueries].slice(0, 40);
    saveStoredList('nlpsql-favorites', favoriteQueries);
    renderSavedQueries();
}

function replayQuery(index) {
    const item = recentQueries[index];
    if (!item) return;
    if (item.schema !== curSchema && allSchemas.includes(item.schema)) {
        switchSchema(item.schema).then(() => { setPrompt(item.question); runQuery(); });
        return;
    }
    setPrompt(item.question);
    runQuery();
}

function replayFavorite(index) {
    const item = favoriteQueries[index];
    if (!item) return;
    const historyIndex = recentQueries.findIndex(saved => saved.question === item.question && saved.schema === item.schema);
    if (historyIndex >= 0) replayQuery(historyIndex);
    else {
        setPrompt(item.question);
        runQuery();
    }
}

function removeFavorite(index) {
    favoriteQueries.splice(index, 1);
    saveStoredList('nlpsql-favorites', favoriteQueries);
    renderSavedQueries();
}

function clearHistory() {
    recentQueries = [];
    saveStoredList('nlpsql-history', recentQueries);
    renderSavedQueries();
}

function pinResults() {
    if (!resultState) return;
    pinnedResults = [{
        question: resultState.question,
        schema: curSchema,
        sql: resultState.sql,
        columns: resultState.columns,
        rows: resultState.rows,
        at: Date.now(),
    }, ...pinnedResults].slice(0, 12);
    saveStoredList('nlpsql-pins', pinnedResults);
    renderPins();
}

function removePin(index) {
    pinnedResults.splice(index, 1);
    saveStoredList('nlpsql-pins', pinnedResults);
    renderPins();
}

function openPin(index) {
    const item = pinnedResults[index];
    if (!item) return;
    resultState = {...item, truncated:false};
    document.getElementById('prompt').value = item.question;
    document.getElementById('sqlOut').value = item.sql;
    updateSqlHighlight();
    document.getElementById('results').style.display = 'flex';
    document.getElementById('resultToolbar').style.display = 'flex';
    document.getElementById('chartPanel').style.display = item.rows.length ? 'block' : 'none';
    document.getElementById('chartType').value = 'auto';
    renderSummary();
    renderResultsTable();
    renderChart();
}

function updateShareLink(question, schema) {
    const params = new URLSearchParams({q:question, schema});
    history.replaceState(null, '', `${location.pathname}${location.search}#${params}`);
}

async function shareQuery() {
    if (!resultState) return;
    updateShareLink(resultState.question, curSchema);
    const url = location.href;
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(url);
            setStatus('ok', 'Link copied');
        } else {
            window.prompt('Copy this shareable query link:', url);
        }
    } catch (error) {
        console.error('Could not copy query link', error);
        window.prompt('Copy this shareable query link:', url);
    }
}

function printReport() {
    if (!resultState) return;
    const report = window.open('', '_blank');
    if (!report) {
        setStatus('ok', 'Allow pop-ups to create a PDF report');
        return;
    }
    report.opener = null;
    const tableRows = [resultState.columns, ...resultState.rows].map(row =>
        `<tr>${row.map(value => `<td>${esc(value ?? '')}</td>`).join('')}</tr>`
    ).join('');
    const chart = document.getElementById('chartOut').innerHTML;
    report.document.write(`<!doctype html><html><head><title>NLPSQL report</title><style>body{font:14px Arial;margin:32px;color:#182230}pre{white-space:pre-wrap;background:#f3f6fb;padding:12px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd4df;padding:6px;text-align:left}svg{max-width:100%;height:auto}.summary{padding:10px;background:#eff6ff}</style></head><body><h1>${esc(resultState.question)}</h1><p>${esc(curSchema)} · ${new Date().toLocaleString()}</p><div class="summary">${esc(document.getElementById('summaryOut').textContent)}</div><h2>Visualization</h2>${chart}<h2>SQL</h2><pre>${esc(resultState.sql)}</pre><h2>Results</h2><table>${tableRows}</table><script>window.onload=()=>window.print();<\/script></body></html>`);
    report.document.close();
}

async function runEditedSql() {
    const sql = document.getElementById('sqlOut').value.trim();
    if (!sql) return;
    showProgress('Validating edited SQL');
    try {
        const response = await fetch('/api/execute_sql', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({sql})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `Execution failed (${response.status})`);
        if (!resultState) resultState = {question:document.getElementById('prompt').value, columns:[], rows:[], truncated:false};
        resultState = {...resultState, sql, columns:data.columns, rows:data.rows, truncated:data.truncated};
        document.getElementById('sqlOut').value = sql;
        updateSqlHighlight();
        document.getElementById('resultToolbar').style.display = 'flex';
        document.getElementById('chartPanel').style.display = data.rows.length ? 'block' : 'none';
        renderSummary();
        renderResultsTable();
        renderChart();
    } catch (error) {
        document.getElementById('tableOut').innerHTML = `<div class="err-box">${esc(error.message)}</div>`;
    } finally {
        hideProgress();
    }
}

function renderFollowups() {
    if (!resultState) return;
    const questions = [
        `Break this down by month: ${resultState.question}`,
        `Show the top 10 results for: ${resultState.question}`,
        `Sort the results descending: ${resultState.question}`,
    ];
    document.getElementById('followups').innerHTML = questions.map(question =>
        `<button class="sug-btn" onclick="runFollowup('${encodeURIComponent(question).replace(/'/g, '%27')}')">↗ ${esc(question.split(':')[0])}</button>`
    ).join('');
}

function runFollowup(encodedQuestion) {
    setPrompt(decodeURIComponent(encodedQuestion));
    runQuery();
}

function renderAutocomplete() {
    const hints = new Set(recentQueries.map(item => item.question));
    currentSchemaTables.forEach(table => {
        hints.add(`Show all rows from ${table.name}`);
        table.columns.forEach(column => hints.add(`Show ${column.name} from ${table.name}`));
    });
    document.getElementById('queryHints').innerHTML = [...hints].slice(0, 100).map(text => `<option value="${esc(text)}"></option>`).join('');
}

function applyChartEdit(question) {
    const lower = question.toLowerCase();
    const chartTypes = ['bar', 'line', 'pie', 'scatter', 'map'];
    const requested = chartTypes.find(type => lower.includes(`${type} chart`) || lower.includes(`make it ${type}`));
    if (requested) document.getElementById('chartType').value = requested;
    if (/sort .*desc|descending|highest first/.test(lower)) {
        const index = numericColumnIndexes()[0];
        if (index !== undefined) resultSort = {column:index, direction:-1};
    } else if (/sort .*asc|ascending|lowest first/.test(lower)) {
        const index = numericColumnIndexes()[0];
        if (index !== undefined) resultSort = {column:index, direction:1};
    }
}

function startVoiceInput() {
    if (voiceRecorder?.state === 'recording') {
        voiceRecorder.stop();
        return;
    }
    if (preferLocalVoice) {
        startLocalVoiceRecording();
        return;
    }
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        preferLocalVoice = true;
        startLocalVoiceRecording();
        return;
    }
    const recognition = new SpeechRecognition();
    voiceRecognition = recognition;
    recognition.lang = document.getElementById('speechLanguage').value;
    recognition.interimResults = false;
    recognition.onstart = () => setStatus('busy', 'Listening… speak now');
    recognition.onresult = event => {
        setPrompt(event.results[0][0].transcript);
        document.getElementById('prompt').focus();
        setStatus('ok', 'Voice captured');
    };
    recognition.onerror = event => {
        voiceRecognition = null;
        if (event.error === 'network') {
            preferLocalVoice = true;
            setStatus('busy', 'Browser speech service unavailable; checking local voice…');
            startLocalVoiceRecording();
            return;
        }
        const messages = {
            'not-allowed': 'Microphone access is blocked. Allow microphone access for this app.',
            'audio-capture': 'No microphone was found. Connect a microphone and try again.',
            'no-speech': 'No speech detected. Click the microphone and try again.',
            'service-not-allowed': 'Browser speech service is blocked. Trying local voice…',
        };
        if (event.error === 'service-not-allowed') {
            preferLocalVoice = true;
            startLocalVoiceRecording();
            return;
        }
        setStatus('ok', messages[event.error] || `Voice input failed: ${event.error}`);
    };
    recognition.onend = () => {
        if (voiceRecognition === recognition) voiceRecognition = null;
    };
    try {
        recognition.start();
    } catch (error) {
        voiceRecognition = null;
        setStatus('ok', `Could not start voice input: ${error.message}`);
    }
}

async function startLocalVoiceRecording() {
    if (voiceStarting) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
        setStatus('ok', 'Local recording requires a browser with microphone recording support.');
        return;
    }
    voiceStarting = true;
    try {
        const statusResponse = await fetch('/api/voice/status');
        if (!statusResponse.ok) throw new Error('Could not check local voice availability.');
        const voiceStatus = await statusResponse.json();
        if (!voiceStatus.local_whisper_available) {
            setStatus('ok', 'Browser speech needs network access. For offline fallback, install faster-whisper with requirements-voice.txt and restart.');
            voiceStarting = false;
            return;
        }
        voiceStream = await navigator.mediaDevices.getUserMedia({audio:true});
        const formats = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
        const mimeType = formats.find(type => MediaRecorder.isTypeSupported(type));
        voiceRecorder = new MediaRecorder(voiceStream, mimeType ? {mimeType} : undefined);
        voiceChunks = [];
        const button = document.getElementById('voiceBtn');
        button.classList.add('voice-recording');
        button.setAttribute('aria-label', 'Stop local voice recording');
        button.title = 'Recording locally — click to stop';
        voiceRecorder.ondataavailable = event => {
            if (event.data.size) voiceChunks.push(event.data);
        };
        voiceRecorder.onerror = event => {
            clearTimeout(voiceStopTimer);
            button.classList.remove('voice-recording');
            button.setAttribute('aria-label', 'Start voice input');
            button.title = 'Voice input; local Whisper is used if browser recognition cannot connect';
            voiceStream?.getTracks().forEach(track => track.stop());
            voiceStream = null;
            voiceStarting = false;
            setStatus('ok', `Local recording failed: ${event.error?.message || 'recording error'}`);
        };
        voiceRecorder.onstop = async () => {
            clearTimeout(voiceStopTimer);
            button.classList.remove('voice-recording');
            button.setAttribute('aria-label', 'Start voice input');
            button.title = 'Voice input; local Whisper is used if browser recognition cannot connect';
            const recording = new Blob(voiceChunks, {type:voiceRecorder.mimeType || 'audio/webm'});
            voiceChunks = [];
            voiceStream.getTracks().forEach(track => track.stop());
            voiceStream = null;
            if (!recording.size) {
                setStatus('ok', 'No audio was recorded. Try again and speak after recording starts.');
                return;
            }
            setStatus('busy', 'Transcribing locally… first use may download the Whisper model.');
            try {
                const language = encodeURIComponent(document.getElementById('speechLanguage').value);
                const response = await fetch(`/api/voice/transcribe?language=${language}`, {
                    method:'POST',
                    headers:{'Content-Type':recording.type || 'audio/webm'},
                    body:recording,
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || 'Local transcription failed.');
                if (!data.text) throw new Error('No speech detected. Try again and speak clearly.');
                setPrompt(data.text);
                document.getElementById('prompt').focus();
                setStatus('ok', 'Voice transcribed locally');
            } catch (error) {
                setStatus('ok', error.message);
            }
        };
        voiceRecorder.start();
        voiceStarting = false;
        setStatus('busy', 'Recording locally — click the microphone to stop');
        voiceStopTimer = setTimeout(() => {
            if (voiceRecorder?.state === 'recording') voiceRecorder.stop();
        }, 12000);
    } catch (error) {
        voiceStarting = false;
        if (voiceStream) voiceStream.getTracks().forEach(track => track.stop());
        voiceStream = null;
        setStatus('ok', error.name === 'NotAllowedError'
            ? 'Microphone access is blocked. Allow microphone access and try again.'
            : `Local voice input failed: ${error.message}`);
    }
}

function speakSummary() {
    if (!('speechSynthesis' in window)) {
        setStatus('ok', 'Text-to-speech is not supported in this browser');
        return;
    }
    const text = document.getElementById('summaryOut').textContent;
    if (!text) {
        setStatus('ok', 'Run a query before reading its summary');
        return;
    }
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

function updateSqlHighlight() {
    const textarea = document.getElementById('sqlOut');
    const highlight = document.getElementById('sqlHighlight');
    if (!textarea || !highlight) return;
    const sql = textarea.value;
    const escaped = esc(sql);
    const tokens = /(--[^\n]*|'(?:''|[^'])*'|\b(?:SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|ON|AS|GROUP|BY|ORDER|HAVING|LIMIT|OFFSET|DISTINCT|COUNT|SUM|AVG|MIN|MAX|CASE|WHEN|THEN|ELSE|END|AND|OR|NOT|NULL|IS|IN|LIKE|BETWEEN|UNION|ALL|ASC|DESC|WITH|OVER|PARTITION|INTO|VALUES|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER)\b|\b\d+(?:\.\d+)?\b)/gi;
    highlight.innerHTML = escaped.replace(tokens, token => {
        const type = token.startsWith('--') ? 'comment' : token.startsWith("'") ? 'string' : /^\d/.test(token) ? 'number' : 'keyword';
        return `<span class="sql-${type}">${token}</span>`;
    }) + '\n';
    syncSqlHighlight();
}

function syncSqlHighlight() {
    const textarea = document.getElementById('sqlOut');
    const highlight = document.getElementById('sqlHighlight');
    if (textarea && highlight) {
        highlight.scrollTop = textarea.scrollTop;
        highlight.scrollLeft = textarea.scrollLeft;
    }
}

function openCommandPalette() {
    document.getElementById('commandOverlay').classList.add('open');
    document.getElementById('commandSearch').focus();
}

function closeCommandPalette() {
    document.getElementById('commandOverlay').classList.remove('open');
}

function filterCommands(value) {
    document.querySelectorAll('.command-list button').forEach(button => {
        button.hidden = !button.textContent.toLowerCase().includes(value.toLowerCase());
    });
}

document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        openCommandPalette();
    } else if (event.key === 'Escape') closeCommandPalette();
    else if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') runQuery();
});

function renderErDiagram(relationships) {
    const host = document.getElementById('erCanvas');
    const tables = currentSchemaTables;
    const width = Math.max(420, tables.length * 190);
    const positions = new Map(tables.map((table, index) => [table.name, {x:25 + index * 190, y:35 + (index % 2) * 145}]));
    let svg = `<svg viewBox="0 0 ${width} 360" role="img" aria-label="Database entity relationship diagram">`;
    (relationships || []).forEach(relation => {
        const from = positions.get(relation.table), to = positions.get(relation.ref_table);
        if (from && to) svg += `<line x1="${from.x + 145}" y1="${from.y + 22}" x2="${to.x}" y2="${to.y + 22}" stroke="var(--purple)" stroke-width="2"><title>${esc(relation.table)}.${esc(relation.column)} → ${esc(relation.ref_table)}.${esc(relation.ref_column)}</title></line>`;
    });
    tables.forEach(table => {
        const point = positions.get(table.name);
        svg += `<rect x="${point.x}" y="${point.y}" width="145" height="90" rx="8" fill="var(--card)" stroke="var(--border-a)"></rect><text x="${point.x + 10}" y="${point.y + 20}" class="chart-label">${esc(table.name)}</text>`;
        table.columns.slice(0, 4).forEach((column, index) => {
            svg += `<text x="${point.x + 10}" y="${point.y + 40 + index * 13}" class="chart-label">${esc(column.name)} · ${esc(column.type)}</text>`;
        });
    });
    host.innerHTML = svg + '</svg>';
    host.hidden = false;
}

async function showErDiagram() {
    const response = await fetch('/api/schema_detail');
    const data = await response.json();
    renderErDiagram(data.relationships || []);
}

async function previewTable(table) {
    const response = await fetch(`/api/sample?table=${encodeURIComponent(table)}`);
    const data = await response.json();
    const panel = document.getElementById('samplePanel');
    panel.hidden = false;
    if (!response.ok) {
        panel.innerHTML = `<div class="err-box">${esc(data.error || 'Could not load sample rows.')}</div>`;
        return;
    }
    panel.innerHTML = `<div class="panel-hdr">${esc(table)} · first ${data.rows.length} rows</div><div class="tbl-wrap"><table><thead><tr>${data.columns.map(column => `<th>${esc(column)}</th>`).join('')}</tr></thead><tbody>${data.rows.map(row => `<tr>${row.map(value => `<td>${esc(value ?? 'NULL')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

async function connectDatabase() {
    const engine = window.prompt('Database engine (sqlite, duckdb, postgresql, or mysql):', 'sqlite');
    if (!engine) return;
    const normalizedEngine = engine.toLowerCase();
    if (!['sqlite','duckdb','postgresql','mysql'].includes(normalizedEngine)) {
        setStatus('ok', 'Choose SQLite, DuckDB, PostgreSQL, or MySQL.');
        return;
    }
    const localFile = ['sqlite','duckdb'].includes(normalizedEngine);
    const connection = window.prompt(localFile ? 'Local database file path (opened read-only):' : `${normalizedEngine} connection string (use a read-only database account):`);
    if (!connection) return;
    const body = localFile ? {engine:normalizedEngine, path:connection} : {engine:normalizedEngine, connection_string:connection};
    const response = await fetch('/api/connect_database', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) {
        setStatus('ok', data.error || 'Database connection failed');
        return;
    }
    curSchema = data.schema_name;
    if (!allSchemas.includes(curSchema)) allSchemas.push(curSchema);
    renderTabs();
    await refreshSchemaPanel();
    setStatus('ok', 'Connected read-only');
}

function handleChartEdit(event) {
    if (!resultState) return;
    const request = event.currentTarget.value;
    applyChartEdit(request);
    renderResultsTable();
    renderChart();
}

function toggleTheme() {
    const theme = document.body.classList.contains('theme-light') ? 'dark' : 'light';
    localStorage.setItem('nlpsql-theme-v2', theme);
    applyTheme(theme);
}

function applyTheme(theme) {
    const light = theme === 'light';
    document.body.classList.toggle('theme-light', light);
    document.body.classList.toggle('theme-dark', !light);
    document.getElementById('themeBtn').textContent = light ? 'Dark theme' : 'Light theme';
}

function isNumericValue(value) {
    return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
}

function numericColumnIndexes() {
    if (!resultState || !resultState.rows.length) return [];
    return resultState.columns.map((_, index) => index).filter(index => {
        const values = resultState.rows.map(row => row[index]).filter(value => value !== null && value !== undefined && value !== '');
        return values.length > 0 && values.every(isNumericValue);
    });
}

function renderSummary() {
    const summary = document.getElementById('summaryOut');
    const {columns, rows, truncated} = resultState;
    let text = `Returned ${rows.length} row${rows.length === 1 ? '' : 's'} across ${columns.length} column${columns.length === 1 ? '' : 's'}.`;
    const numericIndex = numericColumnIndexes()[0];
    if (numericIndex !== undefined && rows.length) {
        const values = rows.map(row => row[numericIndex]).filter(isNumericValue).map(Number);
        const average = values.reduce((total, value) => total + value, 0) / values.length;
        const min = Math.min(...values);
        const max = Math.max(...values);
        const label = String(columns[numericIndex]);
        text += ` Average ${label}: ${average.toLocaleString(undefined, {maximumFractionDigits: 2})} (range ${min.toLocaleString()}–${max.toLocaleString()}).`;
    }
    if (truncated) text += ' The result set was capped by the query engine.';
    summary.textContent = text;
}

function renderResultsTable() {
    if (!resultState) return;
    const {columns, rows, truncated} = resultState;
    const filteredRows = rows.map((row, index) => ({row, index})).filter(({row}) =>
        !resultFilter || row.some(value => String(value ?? '').toLocaleLowerCase().includes(resultFilter.toLocaleLowerCase()))
    );
    if (resultSort.column >= 0) {
        const {column, direction} = resultSort;
        filteredRows.sort((left, right) => {
            const a = left.row[column];
            const b = right.row[column];
            if (isNumericValue(a) && isNumericValue(b)) return (Number(a) - Number(b)) * direction;
            return String(a ?? '').localeCompare(String(b ?? ''), undefined, {numeric: true, sensitivity: 'base'}) * direction;
        });
    }
    const pageCount = Math.max(1, Math.ceil(filteredRows.length / resultPageSize));
    resultPage = Math.min(resultPage, pageCount - 1);
    const start = resultPage * resultPageSize;
    const pageRows = filteredRows.slice(start, start + resultPageSize);
    const out = document.getElementById('tableOut');
    if (!pageRows.length) {
        out.innerHTML = `<div class="empty-res">${rows.length ? 'No rows match this filter.' : '✓ Query returned 0 rows.'}</div>`;
        if (rows.length) out.innerHTML += renderTableControls(0, pageCount);
        return;
    }
    let html = `<div class="table-toolbar">
        <input class="table-control table-filter" type="search" aria-label="Filter results" placeholder="Filter results…" value="${esc(resultFilter)}" oninput="filterResults(this.value)">
        <label>Rows per page <select class="table-control" onchange="changePageSize(this.value)">
            ${[10, 25, 50].map(size => `<option value="${size}"${size === resultPageSize ? ' selected' : ''}>${size}</option>`).join('')}
        </select></label>
    </div><div class="tbl-wrap"><table><thead><tr>`;
    columns.forEach((column, index) => {
        const indicator = resultSort.column === index ? (resultSort.direction === 1 ? ' ▲' : ' ▼') : '';
        html += `<th role="button" tabindex="0" onclick="sortResults(${index})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortResults(${index})}">${esc(String(column))}${indicator}</th>`;
    });
    html += '</tr></thead><tbody>';
    pageRows.forEach(({row}) => {
        html += '<tr>' + row.map(value =>
            `<td>${value !== null && value !== undefined ? esc(String(value)) : '<em>NULL</em>'}</td>`
        ).join('') + '</tr>';
    });
    html += '</tbody></table></div>';
    html += `<div class="row-ct">${truncated ? 'First ' : ''}${rows.length} row${rows.length !== 1 ? 's' : ''}${resultFilter ? ` · ${filteredRows.length} matching` : ''}</div>`;
    html += renderTableControls(filteredRows.length, pageCount);
    out.innerHTML = html;
}

function renderTableControls(rowCount, pageCount) {
    const first = rowCount ? resultPage * resultPageSize + 1 : 0;
    const last = Math.min((resultPage + 1) * resultPageSize, rowCount);
    return `<div class="pagination"><span>${first}–${last} of ${rowCount}</span>
        <button class="tool-btn" type="button" onclick="changePage(-1)" ${resultPage === 0 ? 'disabled' : ''}>Previous</button>
        <span>Page ${resultPage + 1} of ${pageCount}</span>
        <button class="tool-btn" type="button" onclick="changePage(1)" ${resultPage >= pageCount - 1 ? 'disabled' : ''}>Next</button></div>`;
}

function filterResults(value) {
    const input = document.querySelector('.table-filter');
    const selection = input ? input.selectionStart : value.length;
    resultFilter = value;
    resultPage = 0;
    renderResultsTable();
    const nextInput = document.querySelector('.table-filter');
    if (nextInput) {
        nextInput.focus();
        nextInput.setSelectionRange(selection, selection);
    }
}

function changePage(delta) {
    if (delta === -1 || delta === 1) resultPage += delta;
    else if ([10, 25, 50].includes(Number(delta))) {
        resultPageSize = Number(delta);
        resultPage = 0;
    }
    renderResultsTable();
}

function changePageSize(value) {
    changePage(Number(value));
}

function sortResults(column) {
    if (resultSort.column === column) resultSort.direction *= -1;
    else resultSort = {column, direction: 1};
    renderResultsTable();
}

function exportResults(format) {
    if (!resultState) return;
    let contents;
    let mimeType;
    let extension;
    if (format === 'csv') {
        const quote = value => {
            let text = String(value ?? '');
            if (typeof value === 'string' && /^[=+\-@\t\r]/.test(text)) text = "'" + text;
            return `"${text.replace(/"/g, '""')}"`;
        };
        contents = [resultState.columns, ...resultState.rows].map(row => row.map(quote).join(',')).join('\r\n');
        mimeType = 'text/csv;charset=utf-8';
        extension = 'csv';
    } else if (format === 'excel') {
        const xmlEscape = value => String(value ?? '').replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        const rows = [resultState.columns, ...resultState.rows].map(row =>
            `<Row>${row.map(value => {
                const numeric = isNumericValue(value);
                return `<Cell><Data ss:Type="${numeric ? 'Number' : 'String'}">${xmlEscape(value)}</Data></Cell>`;
            }).join('')}</Row>`
        ).join('');
        contents = `<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet ss:Name="Results"><Table>${rows}</Table></Worksheet></Workbook>`;
        mimeType = 'application/vnd.ms-excel;charset=utf-8';
        extension = 'xls';
    } else {
        contents = JSON.stringify({
            question: resultState.question,
            sql: resultState.sql,
            columns: resultState.columns,
            rows: resultState.rows,
        }, null, 2);
        mimeType = 'application/json;charset=utf-8';
        extension = 'json';
    }
    const blob = new Blob([contents], {type: mimeType});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `nlpsql-results.${extension}`;
    link.click();
    URL.revokeObjectURL(url);
}

function renderChart() {
    const chart = document.getElementById('chartOut');
    if (!resultState || !resultState.rows.length) {
        chart.innerHTML = '<div class="chart-empty">No rows available to visualize.</div>';
        return;
    }
    const numericIndexes = numericColumnIndexes();
    const categoryIndex = resultState.columns.findIndex((_, index) => !numericIndexes.includes(index));
    const dateLike = categoryIndex >= 0 && /date|time|month|year/i.test(String(resultState.columns[categoryIndex]));
    const hasLocation = resultState.columns.some(column => /city|state|location/i.test(String(column)));
    const requestedType = document.getElementById('chartType').value;
    const type = requestedType === 'auto' ? (hasLocation ? 'map' : dateLike ? 'line' : numericIndexes.length > 1 && categoryIndex < 0 ? 'scatter' : 'bar') : requestedType;
    const rows = resultState.rows.slice(0, 20);
    if (type === 'map') {
        chart.innerHTML = renderGeoMap(rows, numericIndexes[0]);
        return;
    }
    const categoryLabel = categoryIndex >= 0 ? String(resultState.columns[categoryIndex]) : 'Row';
    if (type === 'scatter') {
        if (numericIndexes.length < 2) {
            chart.innerHTML = '<div class="chart-empty">Scatter plots need at least two numeric columns.</div>';
            return;
        }
        chart.innerHTML = renderScatter(rows, numericIndexes[0], numericIndexes[1]);
        return;
    }
    const measureIndex = numericIndexes.find(index => index !== categoryIndex);
    if (measureIndex === undefined) {
        chart.innerHTML = '<div class="chart-empty">No numeric column is available for a chart.</div>';
        return;
    }
    const points = rows.map((row, index) => ({
        label: categoryIndex >= 0 ? String(row[categoryIndex] ?? '') : `Row ${index + 1}`,
        rawValue: row[measureIndex],
    })).filter(point => isNumericValue(point.rawValue)).map(point => ({
        label: point.label,
        value: Number(point.rawValue),
    }));
    const title = `${resultState.columns[measureIndex]} by ${categoryLabel}`;
    if (type === 'pie') {
        chart.innerHTML = renderPie(points, title);
        return;
    }
    chart.innerHTML = renderCartesian(points, type, title);
}

function renderGeoMap(rows, measureIndex) {
    const locationIndex = resultState.columns.findIndex(column => /city|state|location/i.test(String(column)));
    if (locationIndex < 0) return '<div class="chart-empty">A city, state, location, or region column is required for the geo map.</div>';
    const locations = {
        'andhra pradesh':[16.5,80.6], 'arunachal pradesh':[28.2,94.7], assam:[26.2,92.9], bihar:[25.6,85.1],
        chhattisgarh:[21.3,81.6], goa:[15.5,73.8], gujarat:[22.3,71.2], haryana:[29.1,76.1],
        'himachal pradesh':[31.1,77.2], jharkhand:[23.6,85.3], karnataka:[15.3,75.7], kerala:[10.8,76.3],
        'madhya pradesh':[23.5,77.4], maharashtra:[19.7,75.7], manipur:[24.8,93.9], meghalaya:[25.5,91.3],
        mizoram:[23.2,92.8], nagaland:[26.2,94.1], odisha:[20.9,85.1], punjab:[31.1,75.3],
        rajasthan:[26.9,73.8], sikkim:[27.5,88.5], tamilnadu:[11.1,78.7], 'tamil nadu':[11.1,78.7],
        telangana:[17.9,79.0], tripura:[23.8,91.3], 'uttar pradesh':[26.8,80.9], uttarakhand:[30.1,79.0],
        'west bengal':[22.9,87.8], delhi:[28.6,77.2], mumbai:[19.1,72.9], bengaluru:[12.9,77.6],
        bangalore:[12.9,77.6], chennai:[13.1,80.3], hyderabad:[17.4,78.5], kolkata:[22.6,88.4],
        pune:[18.5,73.9], ahmedabad:[23.0,72.6], jaipur:[26.9,75.8], lucknow:[26.8,80.9],
    };
    const recognized = rows.map(row => {
        const name = String(row[locationIndex] ?? '').trim().toLowerCase();
        return {name:row[locationIndex], point:locations[name], value:measureIndex !== undefined && isNumericValue(row[measureIndex]) ? Number(row[measureIndex]) : 1};
    }).filter(item => item.point);
    if (!recognized.length) return '<div class="chart-empty">No recognized Indian state/city names in the first 20 rows; other regions need coordinates.</div>';
    let svg = '<svg viewBox="0 0 640 430" role="img" aria-label="India geo bubble map"><title>India data locations</title><rect class="geo-bg" x="30" y="25" width="580" height="360" rx="14"></rect>';
    [0,1,2,3,4].forEach(index => {
        const x = 70 + index * 130, y = 55 + index * 70;
        svg += `<line class="geo-grid" x1="${x}" y1="35" x2="${x}" y2="375"></line><line class="geo-grid" x1="40" y1="${y}" x2="600" y2="${y}"></line>`;
    });
    recognized.forEach(item => {
        const [lat, lon] = item.point;
        const x = 70 + (lon - 68) / 30 * 520;
        const y = 45 + (37 - lat) / 31 * 320;
        svg += `<circle class="geo-mark" cx="${x}" cy="${y}" r="8"><title>${esc(item.name)}${Number.isFinite(item.value) ? `: ${esc(item.value)}` : ''}</title></circle><text class="chart-label" x="${x + 8}" y="${y - 7}">${esc(item.name)}</text>`;
    });
    return svg + '</svg>';
}

function renderCartesian(points, type, title) {
    if (!points.length) return '<div class="chart-empty">No numeric values available for a chart.</div>';
    const width = 720, height = 300, left = 48, right = 18, top = 26, bottom = 54;
    const plotWidth = width - left - right, plotHeight = height - top - bottom;
    const min = Math.min(0, ...points.map(point => point.value));
    const max = Math.max(0, ...points.map(point => point.value));
    const range = max - min || 1;
    const y = value => top + (max - value) / range * plotHeight;
    const baseline = y(0);
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(title)} ${type} chart"><title>${esc(title)}</title>`;
    [0, .5, 1].forEach(fraction => {
        const value = min + range * fraction;
        const yPos = y(value);
        svg += `<line class="chart-grid" x1="${left}" y1="${yPos}" x2="${width - right}" y2="${yPos}"></line><text class="chart-label" x="${left - 7}" y="${yPos + 4}" text-anchor="end">${esc(value.toLocaleString(undefined, {maximumFractionDigits: 2}))}</text>`;
    });
    const step = plotWidth / points.length;
    if (type === 'line') {
        const coords = points.map((point, index) => `${left + step * (index + .5)},${y(point.value)}`);
        svg += `<polyline class="chart-line" points="${coords.join(' ')}"></polyline>`;
        points.forEach((point, index) => {
            const x = left + step * (index + .5), yPos = y(point.value);
            svg += `<circle class="chart-mark" cx="${x}" cy="${yPos}" r="4"><title>${esc(point.label)}: ${esc(point.value.toLocaleString())}</title></circle>`;
        });
    } else {
        const barWidth = Math.max(2, step * .68);
        points.forEach((point, index) => {
            const x = left + step * index + (step - barWidth) / 2;
            const yPos = y(point.value);
            svg += `<rect class="chart-mark" x="${x}" y="${Math.min(yPos, baseline)}" width="${barWidth}" height="${Math.max(1, Math.abs(baseline - yPos))}" rx="3"><title>${esc(point.label)}: ${esc(point.value.toLocaleString())}</title></rect>`;
        });
    }
    points.forEach((point, index) => {
        const x = left + step * (index + .5);
        svg += `<text class="chart-label" x="${x}" y="${height - 24}" text-anchor="middle">${esc(point.label.slice(0, 12))}</text>`;
    });
    return svg + '</svg>';
}

function renderScatter(rows, xIndex, yIndex) {
    const pairs = rows.filter(row => isNumericValue(row[xIndex]) && isNumericValue(row[yIndex]))
        .map(row => ({x: Number(row[xIndex]), y: Number(row[yIndex])}))
        .filter(point => Number.isFinite(point.x) && Number.isFinite(point.y));
    if (!pairs.length) return '<div class="chart-empty">No numeric pairs available for a scatter plot.</div>';
    const width = 720, height = 300, left = 54, right = 20, top = 24, bottom = 42;
    const xMin = Math.min(...pairs.map(point => point.x)), xMax = Math.max(...pairs.map(point => point.x));
    const yMin = Math.min(...pairs.map(point => point.y)), yMax = Math.max(...pairs.map(point => point.y));
    const x = value => left + (value - xMin) / (xMax - xMin || 1) * (width - left - right);
    const y = value => top + (yMax - value) / (yMax - yMin || 1) * (height - top - bottom);
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Scatter plot of ${esc(resultState.columns[xIndex])} and ${esc(resultState.columns[yIndex])}"><title>${esc(resultState.columns[yIndex])} by ${esc(resultState.columns[xIndex])}</title>`;
    pairs.forEach(point => {
        svg += `<circle class="chart-mark" cx="${x(point.x)}" cy="${y(point.y)}" r="5"><title>${esc(point.x.toLocaleString())}, ${esc(point.y.toLocaleString())}</title></circle>`;
    });
    svg += `<text class="chart-label" x="${width / 2}" y="${height - 8}" text-anchor="middle">${esc(resultState.columns[xIndex])}</text>`;
    svg += `<text class="chart-label" x="14" y="${height / 2}" transform="rotate(-90 14 ${height / 2})" text-anchor="middle">${esc(resultState.columns[yIndex])}</text>`;
    return svg + '</svg>';
}

function renderPie(points, title) {
    const positivePoints = points.filter(point => point.value > 0);
    const total = positivePoints.reduce((sum, point) => sum + point.value, 0);
    if (!positivePoints.length || !Number.isFinite(total)) return '<div class="chart-empty">Pie charts need positive numeric values.</div>';
    const colors = ['#4f8ef7', '#a78bfa', '#10b981', '#f59e0b', '#f472b6', '#22d3ee'];
    const cx = 180, cy = 145, radius = 112;
    let angle = -Math.PI / 2;
    let svg = `<svg viewBox="0 0 720 300" role="img" aria-label="${esc(title)} pie chart"><title>${esc(title)}</title>`;
    positivePoints.forEach((point, index) => {
        const nextAngle = angle + point.value / total * Math.PI * 2;
        const x1 = cx + radius * Math.cos(angle), y1 = cy + radius * Math.sin(angle);
        const x2 = cx + radius * Math.cos(nextAngle), y2 = cy + radius * Math.sin(nextAngle);
        const largeArc = nextAngle - angle > Math.PI ? 1 : 0;
        svg += `<path d="M ${cx} ${cy} L ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2} Z" fill="${colors[index % colors.length]}"><title>${esc(point.label)}: ${esc(point.value.toLocaleString())}</title></path>`;
        const legendY = 30 + index * 23;
        if (legendY < 286) svg += `<rect x="365" y="${legendY - 10}" width="10" height="10" rx="2" fill="${colors[index % colors.length]}"></rect><text class="chart-label" x="382" y="${legendY}">${esc(point.label.slice(0, 28))}: ${esc(point.value.toLocaleString())}</text>`;
        angle = nextAngle;
    });
    return svg + '</svg>';
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('prompt').addEventListener('keypress', e => {
        if (e.key === 'Enter') runQuery();
    });
});

// ── Upload Modal ──────────────────────────────────────────────────────────────
const FILE_ICONS = {sql:'🗃', csv:'📊', json:'📋', pdf:'📄', png:'🖼', jpg:'🖼', jpeg:'🖼'};

function openUpload()  { document.getElementById('overlay').classList.add('open'); }
function closeUpload() { document.getElementById('overlay').classList.remove('open'); clearFile(); setUploadStatus(''); }
function overlayClick(e) { if (e.target === document.getElementById('overlay')) closeUpload(); }

function onDragOver(e)  { e.preventDefault(); document.getElementById('dropZone').classList.add('drag-over'); }
function onDragLeave(e) { document.getElementById('dropZone').classList.remove('drag-over'); }
function onDrop(e) {
    e.preventDefault();
    document.getElementById('dropZone').classList.remove('drag-over');
    const files = [...e.dataTransfer.files];
    if (files.length) setFile(files);
}
function onFileSelect(e) { if (e.target.files.length) setFile([...e.target.files]); }

function setFile(files) {
    pendingFile = Array.isArray(files) ? files : [files];
    if (pendingFile.length > 8) {
        setUploadStatus('Select no more than 8 files at a time.', 'error');
        pendingFile = null;
        document.getElementById('uploadBtn').disabled = true;
        return;
    }
    const imageFiles = pendingFile.filter(file => /\.(png|jpe?g)$/i.test(file.name));
    if (imageFiles.length && (imageFiles.length > 1 || pendingFile.length > 1)) {
        setUploadStatus('Process one image at a time, separately from data files.', 'error');
        pendingFile = null;
        document.getElementById('uploadBtn').disabled = true;
        return;
    }
    const first = pendingFile[0];
    const ext = first.name.split('.').pop().toLowerCase();
    const totalSize = pendingFile.reduce((total, file) => total + file.size, 0);
    if (totalSize > 20 * 1024 * 1024 || pendingFile.some(file => file.size > 8 * 1024 * 1024)) {
        setUploadStatus('Uploads are limited to 8 MB per file and 20 MB combined.', 'error');
        pendingFile = null;
        document.getElementById('uploadBtn').disabled = true;
        return;
    }
    document.getElementById('fileIcon').textContent = FILE_ICONS[ext] || '📄';
    document.getElementById('fileName').textContent = pendingFile.length === 1 ? first.name : `${pendingFile.length} files`;
    document.getElementById('fileSize').textContent = formatBytes(totalSize);
    document.getElementById('filePreview').classList.add('show');
    document.getElementById('uploadBtn').disabled = false;
    document.getElementById('uploadPreview').innerHTML = '';
    setUploadStatus('');
    if (!imageFiles.length) previewUploadFiles();
}

function clearFile() {
    pendingFile = null;
    document.getElementById('filePreview').classList.remove('show');
    document.getElementById('uploadBtn').disabled = true;
    document.getElementById('fileInput').value = '';
    document.getElementById('uploadPreview').innerHTML = '';
    setUploadStatus('');
}

function setUploadStatus(msg, type='info') {
    const el = document.getElementById('uploadStatus');
    el.className = 'upload-status' + (msg ? ' ' + type : '');
    el.textContent = msg;
}

async function doUpload() {
    if (!pendingFile || !pendingFile.length) return;
    const btn = document.getElementById('uploadBtn');
    btn.disabled = true;
    btn.textContent = 'Importing…';
    setUploadStatus('Reading file…', 'info');

    try {
        const isImage = /\.(png|jpe?g)$/i.test(pendingFile[0].name);
        if (isImage) {
            setUploadStatus('Running local OCR…', 'info');
            const imageContent = await readFileAsBase64(pendingFile[0]);
            const ocrResponse = await fetch('/api/ocr', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({filename:pendingFile[0].name, content_b64:imageContent})});
            const ocrResult = await ocrResponse.json();
            if (!ocrResponse.ok) throw new Error(ocrResult.error || 'OCR failed');
            document.getElementById('uploadPreview').innerHTML = `<p>Review and format OCR text as comma-separated values, including a header row:</p><textarea id="ocrData" class="sql-editor" style="min-height:130px">${esc(ocrResult.text)}</textarea><button class="tool-btn" type="button" onclick="useOcrData()">Use edited OCR text as CSV</button>`;
            setUploadStatus('OCR complete. Review the extracted text before importing it as CSV.', 'success');
            btn.disabled = true;
            btn.textContent = 'Import Schema';
            return;
        }
        const filePayloads = await Promise.all(pendingFile.map(async (file, index) => {
            const typeOverrides = {};
            document.querySelectorAll(`[data-file-index="${index}"][data-column]`).forEach(select => {
                typeOverrides[select.dataset.column] = select.value;
            });
            return {filename:file.name, content_b64:await readFileAsBase64(file), type_overrides:typeOverrides};
        }));
        setUploadStatus('Uploading to server…', 'info');

        const resp = await fetch('/api/upload_schema', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({files:filePayloads})
        }).then(r => r.json());

        if (resp.error) {
            setUploadStatus('❌ ' + resp.error, 'error');
            btn.disabled = false;
            btn.textContent = 'Import Schema';
            return;
        }

        // Success
        const tabName = resp.schema_name;
        uploadedSet.add(tabName);
        if (!allSchemas.includes(tabName)) allSchemas.push(tabName);

        // Add custom suggestions
        SUGGESTIONS[tabName] = resp.table_names.flatMap(t => [
            `Show all rows from ${t}`,
            `Count the rows in ${t}`,
        ]);

        setUploadStatus(`✓ Loaded "${tabName}" — ${resp.table_count} table(s), ${resp.row_count} row(s) total`, 'success');

        // Switch to the uploaded schema
        renderTabs();
        setTimeout(async () => {
            closeUpload();
            await switchSchema(tabName);
        }, 1200);
    } catch(e) {
        setUploadStatus('❌ ' + e.message, 'error');
        btn.disabled = false;
        btn.textContent = 'Import Schema';
    }
}

function useOcrData() {
    const text = document.getElementById('ocrData').value;
    if (!text.trim()) {
        setUploadStatus('OCR returned no text. Try a clearer image.', 'error');
        return;
    }
    pendingFile = [new File([text], 'ocr_table.csv', {type:'text/csv'})];
    document.getElementById('fileName').textContent = 'ocr_table.csv';
    document.getElementById('fileSize').textContent = formatBytes(new Blob([text]).size);
    document.getElementById('uploadPreview').innerHTML = '';
    document.getElementById('uploadBtn').disabled = false;
    previewUploadFiles();
}

async function previewUploadFiles() {
    if (!pendingFile || !pendingFile.length) return;
    const unsupported = pendingFile.find(file => !/\.(sql|csv|json|pdf)$/i.test(file.name));
    if (unsupported) {
        setUploadStatus(`Unsupported file type: ${esc(unsupported.name)}`, 'error');
        return;
    }
    try {
        const files = await Promise.all(pendingFile.map(async file => ({
            filename:file.name,
            content_b64:await readFileAsBase64(file),
        })));
        const response = await fetch('/api/preview_upload', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({files})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not preview upload');
        const uploadPreviewData = data.tables || [];
        document.getElementById('uploadPreview').innerHTML = uploadPreviewData.map(table => `
            <section><strong>${esc(table.name)}</strong><div class="tbl-wrap"><table>
            <thead><tr>${table.columns.map(column => `<th>${esc(column.name)} ${table.type_editable ? `<select class="type-select" data-file-index="${table.file_index}" data-column="${esc(column.name)}"><option${column.type==='INTEGER'?' selected':''}>INTEGER</option><option${column.type==='REAL'?' selected':''}>REAL</option><option${column.type==='TEXT'?' selected':''}>TEXT</option></select>` : `<span class="ct">${esc(column.type)}</span>`}</th>`).join('')}</tr></thead>
            <tbody>${table.rows.map(row => `<tr>${row.map(value => `<td>${esc(value ?? 'NULL')}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section>`).join('');
        if (data.relationships?.length) {
            document.getElementById('uploadPreview').innerHTML += `<p>Detected links: ${data.relationships.map(item => `${esc(item.table)}.${esc(item.column)} → ${esc(item.ref_table)}.${esc(item.ref_column)}`).join(' · ')}</p>`;
        }
    } catch (error) {
        setUploadStatus(error.message, 'error');
    }
}

function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            // result is "data:<mime>;base64,<data>"
            const b64 = reader.result.split(',')[1];
            resolve(b64);
        };
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(s) {
    return String(s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function formatBytes(n) {
    if (n < 1024) return n + ' B';
    if (n < 1024*1024) return (n/1024).toFixed(1) + ' KB';
    return (n/1024/1024).toFixed(1) + ' MB';
}

function setStatus(type, msg) {
    const el = document.getElementById('statusDot');
    el.textContent = msg;
    el.className = 'status-dot' + (type==='busy' ? ' busy' : '');
}

// Boot
init();
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        global current_schema_name, current_db_path, adapter
        url_path = urllib.parse.urlparse(self.path).path

        if url_path in ("/", "/index.html"):
            page = (
                HTML_PAGE
                .replace("'__SCHEMA__'", json.dumps(current_schema_name))
                .replace("__SUGGESTIONS__", json.dumps(SCHEMA_SUGGESTIONS))
            )
            self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))

        elif url_path == "/api/schemas":
            data = {
                "available_schemas": list_available_schemas() + sorted(set(uploaded_schemas) | set(connected_databases)),
                "current_schema": current_schema_name,
                "current_db_path": current_db_path,
                "uploaded_schemas": sorted(uploaded_schemas.keys()),
                "tables": [t.name for t in adapter.get_schema().tables],
            }
            self._json(data)

        elif url_path == "/api/schema_detail":
            schema = adapter.get_schema()
            fk_cols = set()
            relationships = []
            for tbl in schema.tables:
                for column, ref_table, ref_column in tbl.foreign_keys:
                    fk_cols.add((tbl.name, column))
                    relationships.append({
                        "table": tbl.name,
                        "column": column,
                        "ref_table": ref_table,
                        "ref_column": ref_column,
                    })
                for col in tbl.columns:
                    if col.name.endswith("_id") and not col.is_pk:
                        fk_cols.add((tbl.name, col.name))
            for relation in _infer_relationships(schema):
                if relation not in relationships:
                    relationships.append(relation)
            tables_data = []
            for tbl in schema.tables:
                tables_data.append({
                    "name": tbl.name,
                    "columns": [
                        {
                            "name": col.name,
                            "type": col.type,
                            "is_pk": col.is_pk,
                            "is_fk": (tbl.name, col.name) in fk_cols,
                        }
                        for col in tbl.columns
                    ],
                })
            relationships.extend(
                relation for relation in uploaded_relationships.get(current_schema_name, [])
                if relation not in relationships
            )
            self._json({
                "tables": tables_data,
                "relationships": relationships,
                "engine": current_db_engine.upper(),
                "schema_name": current_schema_name,
            })

        elif url_path == "/api/sample":
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            table_name = params.get("table", [""])[0]
            schema = adapter.get_schema()
            if not any(table.name == table_name for table in schema.tables):
                self._json({"error": "Unknown table."}, 404)
                return
            quoted_table = '"' + table_name.replace('"', '""') + '"'
            try:
                result = adapter.execute_readonly(f"SELECT * FROM {quoted_table} LIMIT 10;")
            except Exception as error:
                self._json({"error": str(error)}, 400)
                return
            self._json({"columns": result.columns, "rows": _json_rows(result.rows)})

        elif url_path == "/api/voice/status":
            try:
                import faster_whisper  # noqa: F401
                whisper_available = True
            except ImportError:
                whisper_available = False
            self._json({
                "local_whisper_available": whisper_available,
                "model": os.environ.get("WHISPER_MODEL", "tiny"),
            })

        else:
            self._send(404, "text/plain", b"Not Found")

    def do_POST(self):
        global current_schema_name, current_db_path, current_db_engine, DB_ENGINE
        global pipeline, adapter, knowledge_engine, provider, uploaded_relationships
        url_path = urllib.parse.urlparse(self.path).path
        body = self._read_body()
        if body is None:
            return

        if url_path == "/api/voice/transcribe":
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            language = params.get("language", ["en-IN"])[0]
            try:
                text = transcribe_local_audio(
                    body,
                    language,
                    self.headers.get("Content-Type", ""),
                )
            except ValueError as error:
                self._json({"error": str(error)}, 400)
            except RuntimeError as error:
                status = 501 if "not installed" in str(error) else 500
                self._json({"error": str(error)}, status)
            except Exception as error:
                self._json({"error": f"Local voice transcription failed: {error}"}, 500)
            else:
                self._json({"text": text})
            return

        if url_path == "/api/switch_schema":
            params = self._parse_json(body)
            if params is None:
                return
            new_schema = params.get("schema", "company")
            new_path   = params.get("path")

            if new_schema in connected_databases and not new_path:
                engine, db_path = connected_databases[new_schema]
                try:
                    new_pipeline, new_adapter, new_knowledge = _build_connection_pipeline(
                        engine, db_path, provider
                    )
                    old_adapter = adapter
                    pipeline, adapter, knowledge_engine = new_pipeline, new_adapter, new_knowledge
                    current_schema_name = new_schema
                    current_db_path = db_path
                    current_db_engine = engine
                    DB_ENGINE = engine
                    old_adapter.close()
                    self._json({"status":"ok", "current_schema":new_schema, "tables":[table.name for table in adapter.get_schema().tables]})
                except Exception as error:
                    self._json({"error":_redact_connection_error(error)}, 400)
                return

            # Check if it's an uploaded schema
            if new_schema in uploaded_schemas and not new_path:
                seed_sql = uploaded_schemas[new_schema]
                try:
                    inferred_relationships = uploaded_relationships.get(new_schema, [])
                    new_adapter = SQLiteAdapter(
                        seed_sql=seed_sql,
                        foreign_key_overrides=[
                            (item["table"], item["column"], item["ref_table"], item["ref_column"])
                            for item in inferred_relationships
                        ],
                    )
                    from core.pipeline import Pipeline
                    from core.retriever import SchemaRetriever
                    from core.knowledge import DomainKnowledgeEngine
                    from core.few_shot import FewShotRetriever
                    from core.agents.drafter import SQLDrafter
                    from core.agents.classifier import QueryClassifier
                    from core.agents.critic import SemanticCritic
                    new_knowledge = DomainKnowledgeEngine(new_adapter)
                    few_shot = FewShotRetriever(examples=[], top_k=2)
                    drafter  = SQLDrafter(provider=provider, few_shot_retriever=few_shot)
                    new_pipeline = Pipeline(
                        adapter=new_adapter, generator=drafter,
                        retriever=SchemaRetriever(),
                        knowledge_engine=new_knowledge,
                        classifier=QueryClassifier(), critic=SemanticCritic(),
                    )
                    old_adapter = adapter
                    adapter         = new_adapter
                    pipeline        = new_pipeline
                    knowledge_engine = new_knowledge
                    current_schema_name = new_schema
                    current_db_path = None
                    current_db_engine = "sqlite"
                    DB_ENGINE = "sqlite"
                    old_adapter.close()
                    print(f"Switched to uploaded schema '{new_schema}'")
                    self._json({"status": "ok", "current_schema": current_schema_name, "tables": [t.name for t in adapter.get_schema().tables]})
                except Exception as e:
                    self._json({"error": str(e)}, 400)
                return

            # Built-in schema
            try:
                schema_engine = DB_ENGINE if DB_ENGINE in {"sqlite", "duckdb"} else "sqlite"
                new_pipeline, new_adapter, new_knowledge = build_pipeline(
                    db_engine=schema_engine, db_path=new_path,
                    schema_name=new_schema, provider=provider,
                )
                old_adapter = adapter
                pipeline, adapter, knowledge_engine = new_pipeline, new_adapter, new_knowledge
                current_schema_name = new_schema
                current_db_path     = new_path
                current_db_engine = schema_engine
                DB_ENGINE = schema_engine
                old_adapter.close()
                print(f"Switched to schema='{new_schema}'")
                self._json({"status": "ok", "current_schema": current_schema_name, "tables": [t.name for t in adapter.get_schema().tables]})
            except Exception as e:
                self._json({"error": str(e)}, 400)
            return

        if url_path == "/api/connect_database":
            params = self._parse_json(body)
            if params is None:
                return
            engine = str(params.get("engine", "")).lower()
            is_local_file = engine in {"sqlite", "duckdb"}
            connection = params.get("path") if is_local_file else params.get("connection_string")
            if engine not in {"sqlite", "duckdb", "postgresql", "mysql"} or not isinstance(connection, str) or not connection.strip():
                self._json({"error": "Choose SQLite, DuckDB, PostgreSQL, or MySQL and provide its file path or connection string."}, 400)
                return
            try:
                if not is_local_file:
                    expected_prefix = "postgresql://" if engine == "postgresql" else "mysql://"
                    accepted_prefixes = (
                        ("postgresql://", "postgres://", "postgresql+psycopg://")
                        if engine == "postgresql"
                        else ("mysql://", "mysql+pymysql://")
                    )
                    if not connection.strip().lower().startswith(accepted_prefixes):
                        raise ValueError(f"Connection string must start with {expected_prefix}.")
                new_pipeline, new_adapter, new_knowledge = _build_connection_pipeline(
                    engine, connection.strip(), provider
                )
            except Exception as error:
                self._json({"error": _redact_connection_error(error)}, 400)
                return
            old_adapter = adapter
            pipeline, adapter, knowledge_engine = new_pipeline, new_adapter, new_knowledge
            DB_ENGINE = engine
            current_db_engine = engine
            current_db_path = connection.strip() if is_local_file else None
            if is_local_file:
                base_name = "local_" + (os.path.splitext(os.path.basename(connection.strip()))[0] or "database")
            else:
                parsed_connection = urllib.parse.urlsplit(connection.strip())
                base_name = re.sub(r"[^\w-]", "_", parsed_connection.path.strip("/") or parsed_connection.hostname or "database")[:30]
            current_schema_name = base_name
            suffix = 2
            while current_schema_name in set(list_available_schemas()) | set(uploaded_schemas) | set(connected_databases):
                current_schema_name = f"{base_name}_{suffix}"
                suffix += 1
            connected_databases[current_schema_name] = (engine, connection.strip())
            old_adapter.close()
            self._json({"status": "ok", "schema_name": current_schema_name, "tables": [table.name for table in adapter.get_schema().tables]})
            return

        if url_path == "/api/preview_upload":
            params = self._parse_json(body)
            if params is None:
                return
            try:
                seed_sql, table_names, relationships, table_sources = _prepare_uploaded_schema(params)
                decoded_files = _decode_upload_files(params)
                preview = SQLiteAdapter(seed_sql=seed_sql)
                try:
                    table_previews = []
                    for table_index, table in enumerate(preview.get_schema().tables):
                        quoted_table = '"' + table.name.replace('"', '""') + '"'
                        rows = preview.execute_readonly(f"SELECT * FROM {quoted_table} LIMIT 5;")
                        file_index = table_sources[table_index] if table_index < len(table_sources) else 0
                        type_editable = (
                            decoded_files[file_index][0].rsplit(".", 1)[-1].lower() in {"csv", "json"}
                            if file_index < len(decoded_files)
                            else False
                        )
                        table_previews.append({
                            "name": table.name,
                            "file_index": file_index,
                            "type_editable": type_editable,
                            "columns": [{"name": column.name, "type": column.type} for column in table.columns],
                            "rows": _json_rows(rows.rows),
                        })
                finally:
                    preview.close()
            except (ValueError, ImportError, sqlite3.Error) as error:
                self._json({"error": str(error)}, 400)
                return
            self._json({"tables": table_previews, "table_names": table_names, "relationships": relationships})
            return

        if url_path == "/api/ocr":
            params = self._parse_json(body)
            if params is None:
                return
            executable = shutil.which("tesseract")
            if not executable:
                self._json({"error": "Local OCR requires Tesseract OCR. Install it and add it to PATH; images are not sent to an external service."}, 501)
                return
            try:
                content = base64.b64decode(params.get("content_b64", ""), validate=True)
            except (ValueError, TypeError):
                self._json({"error": "Invalid base64 image content."}, 400)
                return
            if not content or len(content) > 8 * 1024 * 1024:
                self._json({"error": "Provide an image up to 8 MB."}, 400)
                return
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temporary_file:
                    temporary_file.write(content)
                    temporary_path = temporary_file.name
                process = subprocess.run(
                    [executable, temporary_path, "stdout", "--psm", "6"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    check=False,
                )
                if process.returncode:
                    raise RuntimeError(process.stderr.strip() or "Tesseract failed to read this image.")
                self._json({"text": process.stdout.strip()})
            except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
                self._json({"error": f"OCR failed: {error}"}, 400)
            finally:
                if temporary_path and os.path.exists(temporary_path):
                    os.remove(temporary_path)
            return

        if url_path == "/api/upload_schema":
            params = self._parse_json(body)
            if params is None:
                return
            try:
                seed_sql, table_names, relationships, _ = _prepare_uploaded_schema(params)
            except (ValueError, ImportError, sqlite3.Error) as e:
                self._json({"error": str(e)}, 400)
                return

            filenames = [filename for filename, _, _ in _decode_upload_files(params)]
            stem = "_".join(os.path.splitext(os.path.basename(filename))[0] for filename in filenames)
            tab_name = re.sub(r"[^\w]", "_", stem)[:30] or "custom"
            # Avoid name collision with built-in schemas
            base = tab_name
            idx = 2
            existing = set(list_available_schemas()) | set(uploaded_schemas.keys())
            while tab_name in existing:
                tab_name = f"{base}_{idx}"
                idx += 1

            uploaded_schemas[tab_name] = seed_sql
            uploaded_relationships[tab_name] = relationships

            # Count imported rows and always release the temporary in-memory database.
            tmp = SQLiteAdapter(seed_sql=seed_sql)
            try:
                row_count = 0
                for tbl in tmp.get_schema().tables:
                    quoted_table = '"' + tbl.name.replace('"', '""') + '"'
                    res = tmp.execute_readonly(f"SELECT COUNT(*) FROM {quoted_table};")
                    if res.rows:
                        row_count += res.rows[0][0]
            finally:
                tmp.close()

            print(f"Uploaded schema '{tab_name}': {len(table_names)} table(s)")
            self._json({
                "status":      "ok",
                "schema_name": tab_name,
                "table_names": table_names,
                "table_count": len(table_names),
                "row_count":   row_count,
                "relationships": relationships,
            })
            return

        if url_path in {"/api/query", "/api/query_stream"}:
            params = self._parse_json(body)
            if params is None:
                return
            prompt = params.get("prompt", "")
            if not prompt:
                self._json({"error": "Missing 'prompt' parameter"}, 400)
                return

            print(f"[{current_schema_name}] {prompt}")
            if url_path == "/api/query_stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                client_connected = True

                def send_progress(stage: str, message: str) -> None:
                    nonlocal client_connected
                    if not client_connected:
                        return
                    payload = json.dumps({"type": "progress", "stage": stage, "message": message})
                    try:
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except OSError as error:
                        client_connected = False
                        print(f"Query progress client disconnected: {error}", file=sys.stderr)

                out = pipeline.ask(prompt, on_progress=send_progress)
            else:
                out = pipeline.ask(prompt)
            inf_t = sum(v for k, v in out.timings.items() if k.startswith("generate"))
            q_t   = sum(v for k, v in out.timings.items() if k.startswith("execute"))
            data = {
                "sql":            out.sql,
                "sql_history":    out.sql_history,
                "columns":        out.result.columns if out.result else [],
                "rows":           _json_rows(out.result.rows) if out.result else [],
                "truncated":      out.result.truncated if out.result else False,
                "error":          out.error,
                "attempts":       out.attempts,
                "timings":        out.timings,
                "inference_time": inf_t,
                "query_time":     q_t,
                "is_translated":  out.is_translated,
                "translated_query": out.question if out.is_translated else None,
                "original_query": out.original_question,
            }
            if url_path == "/api/query_stream":
                if client_connected:
                    payload = json.dumps({"type": "result", "data": data})
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
            else:
                self._json(data)
            return

        if url_path == "/api/execute_sql":
            params = self._parse_json(body)
            if params is None:
                return
            sql = params.get("sql")
            if not isinstance(sql, str) or not sql.strip():
                self._json({"error": "Provide a SQL SELECT query."}, 400)
                return
            from core.validator import ValidationError, validate
            from core.adapters.base import ExecutionError
            try:
                validate(sql, adapter.get_schema(), adapter.dialect())
                result = adapter.execute_readonly(sql)
            except (ValidationError, ExecutionError) as error:
                self._json({"error": str(error)}, 400)
                return
            self._json({"columns": result.columns, "rows": _json_rows(result.rows), "truncated": result.truncated})
            return

        self._send(404, "text/plain", b"Not Found")

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._json({"error":"Invalid Content-Length header."}, 400)
            return None
        if length < 0 or length > 28 * 1024 * 1024:
            self._json({"error":"Request body exceeds the 28 MB limit."}, 413)
            return None
        return self.rfile.read(length)

    def _parse_json(self, body: bytes) -> dict | None:
        try:
            value = json.loads(body.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("JSON request body must be an object.")
            return value
        except Exception:
            self._json({"error": "Invalid JSON"}, 400)
            return None

    def _json(self, data: dict, status: int = 200):
        self._send(status, "application/json", json.dumps(data).encode("utf-8"))

    def _send(self, status: int, content_type: str, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # Suppress default access log


def run_server(port: int = 8000):
    httpd = HTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"\n{'='*52}")
    print(f"  NLP-SQL  ->  http://localhost:{port} (local-only)")
    print(f"  Built-in schemas : {list_available_schemas()}")
    print(f"  Active schema    : {current_schema_name}")
    print(f"{'='*52}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")


if __name__ == "__main__":
    run_server()
