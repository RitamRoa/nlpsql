import base64
import json
import os
import urllib.parse
import sys
import io
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
pipeline, adapter, knowledge_engine = build_pipeline(
    db_engine=DB_ENGINE,
    db_path=current_db_path,
    schema_name=current_schema_name,
    provider=provider,
)

# Uploaded custom schemas: {display_name: seed_sql}
uploaded_schemas: dict[str, str] = {}

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
    <title>Natural Language to SQL — Local Engine</title>
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
    </style>
</head>
<body>
<div class="wrap">
    <header>
        <div class="eyebrow">⚡ Local AI — No GPU Required</div>
        <h1>Text to SQL Engine</h1>
        <p>Query any database in plain English — or upload your own schema</p>
    </header>

    <!-- Schema / Tab Bar -->
    <div class="schema-bar" id="schemaBar">
        <span class="bar-label">Dataset</span>
        <div class="schema-tabs" id="schemaTabs"></div>
        <button class="upload-btn" onclick="openUpload()">⬆ Upload Schema</button>
        <span class="status-dot" id="statusDot">Connected</span>
    </div>

    <div class="grid">
        <!-- Schema Explorer -->
        <div class="card schema-panel" id="schemaPanel">
            <div class="panel-hdr">
                <span>Schema Explorer</span>
                <span class="db-badge" id="dbBadge">SQLite</span>
            </div>
            <div id="schemaContent"><div class="loading-msg">Loading…</div></div>
        </div>

        <!-- Query + Results -->
        <div class="card query-panel">
            <div>
                <div class="slabel">Ask a question in plain English</div>
                <div class="inp-wrap">
                    <input type="text" id="prompt" autocomplete="off"
                        placeholder="e.g. Show the highest paid employee in each department">
                    <span class="inp-icon">⌕</span>
                </div>
            </div>

            <div>
                <div class="slabel">Quick Queries</div>
                <div class="suggestions" id="suggestions" style="margin-top:.45rem;"></div>
            </div>

            <button id="runBtn" onclick="runQuery()">
                <span id="btnTxt">Generate &amp; Execute SQL</span>
                <div class="spin" id="spin"></div>
            </button>

            <div class="results" id="results">
                <div id="transBanner" style="display:none;background:rgba(99,157,255,0.1);border:1px solid rgba(99,157,255,0.3);border-radius:8px;padding:.5rem .8rem;font-size:.85rem;color:#93c5fd;margin-bottom:.5rem;"></div>
                <div class="sql-box"><code id="sqlOut"></code></div>
                <div class="stats">
                    ⚡ LLM: <span id="tInf">-</span>
                    &nbsp;🗄 Query: <span id="tQ">-</span>
                    &nbsp;🔁 Attempts: <span id="tAtt">-</span>
                </div>
                <div id="tableOut"></div>
            </div>
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
            <input type="file" id="fileInput" accept=".sql,.csv,.json,.pdf"
                   onchange="onFileSelect(event)">
            <div class="drop-icon">📂</div>
            <div class="drop-title">Drop file here or click to browse</div>
            <div class="drop-sub">Drag &amp; drop your schema file</div>
            <div class="drop-types">
                <span class="type-chip">.sql</span>
                <span class="type-chip">.csv</span>
                <span class="type-chip">.json</span>
                <span class="type-chip">.pdf</span>
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

const SUGGESTIONS = __SUGGESTIONS__;

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
    const d = await fetch('/api/schemas').then(r => r.json());
    allSchemas  = d.available_schemas || [];
    curSchema   = d.current_schema   || 'company';
    uploadedSet = new Set(d.uploaded_schemas || []);
    renderTabs();
    renderSuggestions(curSchema);
    await refreshSchemaPanel();
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
    if (!tables.length) {
        document.getElementById('schemaContent').innerHTML = '<div class="loading-msg">No tables found.</div>';
        return;
    }
    document.getElementById('schemaContent').innerHTML = tables.map(t => `
        <div class="tbl-block">
            <div class="tbl-name">${esc(t.name)}</div>
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
    document.getElementById('suggestions').innerHTML = list.map(s =>
        `<button class="sug-btn" onclick="setPrompt(this.innerText)">${s}</button>`
    ).join('') || '<span style="color:var(--tm);font-size:.82rem;">No suggestions for this schema yet.</span>';
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

    try {
        const d = await fetch('/api/query', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({prompt: q})
        }).then(r => r.json());

        document.getElementById('results').style.display = 'flex';
        const tb = document.getElementById('transBanner');
        if (d.is_translated && d.translated_query) {
            tb.style.display = 'block';
            tb.innerHTML = `🌐 <strong>Translated to English:</strong> "${esc(d.translated_query)}"`;
        } else {
            tb.style.display = 'none';
        }
        document.getElementById('sqlOut').textContent = d.sql || '-- No SQL generated';
        document.getElementById('tInf').textContent = d.inference_time != null ? d.inference_time.toFixed(3)+'s' : '-';
        document.getElementById('tQ').textContent   = d.query_time    != null ? d.query_time.toFixed(4)+'s'    : '-';
        document.getElementById('tAtt').textContent = d.attempts ?? '-';

        const out = document.getElementById('tableOut');
        if (d.error) {
            out.innerHTML = `<div class="err-box"><strong>⚠ Error:</strong><br>${esc(d.error)}</div>`;
        } else if (d.rows?.length === 0) {
            out.innerHTML = '<div class="empty-res">✓ Query returned 0 rows.</div>';
        } else if (d.columns && d.rows) {
            let html = '<div class="tbl-wrap"><table><thead><tr>';
            d.columns.forEach(c => { html += `<th>${esc(String(c))}</th>`; });
            html += '</tr></thead><tbody>';
            d.rows.forEach(row => {
                html += '<tr>' + row.map(v =>
                    `<td>${v !== null && v !== undefined ? esc(String(v)) : '<em>NULL</em>'}</td>`
                ).join('') + '</tr>';
            });
            html += '</tbody></table></div>';
            html += `<div class="row-ct">${d.truncated ? 'First ' : ''}${d.rows.length} row${d.rows.length!==1?'s':''}</div>`;
            out.innerHTML = html;
        }
    } catch(e) {
        document.getElementById('results').style.display = 'flex';
        document.getElementById('tableOut').innerHTML = `<div class="err-box"><strong>Network Error:</strong><br>${esc(e.message)}</div>`;
    } finally {
        btn.disabled = false;
        document.getElementById('spin').style.display = 'none';
        document.getElementById('btnTxt').style.display = 'block';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('prompt').addEventListener('keypress', e => {
        if (e.key === 'Enter') runQuery();
    });
});

// ── Upload Modal ──────────────────────────────────────────────────────────────
const FILE_ICONS = {sql:'🗃', csv:'📊', json:'📋', pdf:'📄'};

function openUpload()  { document.getElementById('overlay').classList.add('open'); }
function closeUpload() { document.getElementById('overlay').classList.remove('open'); clearFile(); setUploadStatus(''); }
function overlayClick(e) { if (e.target === document.getElementById('overlay')) closeUpload(); }

function onDragOver(e)  { e.preventDefault(); document.getElementById('dropZone').classList.add('drag-over'); }
function onDragLeave(e) { document.getElementById('dropZone').classList.remove('drag-over'); }
function onDrop(e) {
    e.preventDefault();
    document.getElementById('dropZone').classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file) setFile(file);
}
function onFileSelect(e) { const file = e.target.files[0]; if (file) setFile(file); }

function setFile(file) {
    pendingFile = file;
    const ext = file.name.split('.').pop().toLowerCase();
    document.getElementById('fileIcon').textContent = FILE_ICONS[ext] || '📄';
    document.getElementById('fileName').textContent = file.name;
    document.getElementById('fileSize').textContent = formatBytes(file.size);
    document.getElementById('filePreview').classList.add('show');
    document.getElementById('uploadBtn').disabled = false;
    setUploadStatus('');
}

function clearFile() {
    pendingFile = null;
    document.getElementById('filePreview').classList.remove('show');
    document.getElementById('uploadBtn').disabled = true;
    document.getElementById('fileInput').value = '';
    setUploadStatus('');
}

function setUploadStatus(msg, type='info') {
    const el = document.getElementById('uploadStatus');
    el.className = 'upload-status' + (msg ? ' ' + type : '');
    el.textContent = msg;
}

async function doUpload() {
    if (!pendingFile) return;
    const btn = document.getElementById('uploadBtn');
    btn.disabled = true;
    btn.textContent = 'Importing…';
    setUploadStatus('Reading file…', 'info');

    try {
        const bytes = await readFileAsBase64(pendingFile);
        setUploadStatus('Uploading to server…', 'info');

        const resp = await fetch('/api/upload_schema', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({filename: pendingFile.name, content_b64: bytes})
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
                "available_schemas": list_available_schemas() + sorted(uploaded_schemas.keys()),
                "current_schema": current_schema_name,
                "current_db_path": current_db_path,
                "uploaded_schemas": sorted(uploaded_schemas.keys()),
                "tables": [t.name for t in adapter.get_schema().tables],
            }
            self._json(data)

        elif url_path == "/api/schema_detail":
            schema = adapter.get_schema()
            fk_cols = set()
            for tbl in schema.tables:
                for col in tbl.columns:
                    if col.name.endswith("_id") and not col.is_pk:
                        fk_cols.add((tbl.name, col.name))
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
            self._json({"tables": tables_data, "engine": DB_ENGINE.upper(), "schema_name": current_schema_name})

        else:
            self._send(404, "text/plain", b"Not Found")

    def do_POST(self):
        global current_schema_name, current_db_path, pipeline, adapter, knowledge_engine, provider
        url_path = urllib.parse.urlparse(self.path).path
        body = self._read_body()

        if url_path == "/api/switch_schema":
            params = self._parse_json(body)
            if params is None:
                return
            new_schema = params.get("schema", "company")
            new_path   = params.get("path")

            # Check if it's an uploaded schema
            if new_schema in uploaded_schemas and not new_path:
                seed_sql = uploaded_schemas[new_schema]
                try:
                    new_adapter = SQLiteAdapter(seed_sql=seed_sql)
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
                    adapter         = new_adapter
                    pipeline        = new_pipeline
                    knowledge_engine = new_knowledge
                    current_schema_name = new_schema
                    current_db_path = None
                    print(f"Switched to uploaded schema '{new_schema}'")
                    self._json({"status": "ok", "current_schema": current_schema_name, "tables": [t.name for t in adapter.get_schema().tables]})
                except Exception as e:
                    self._json({"error": str(e)}, 400)
                return

            # Built-in schema
            try:
                pipeline, adapter, knowledge_engine = build_pipeline(
                    db_engine=DB_ENGINE, db_path=new_path,
                    schema_name=new_schema, provider=provider,
                )
                current_schema_name = new_schema
                current_db_path     = new_path
                print(f"Switched to schema='{new_schema}'")
                self._json({"status": "ok", "current_schema": current_schema_name, "tables": [t.name for t in adapter.get_schema().tables]})
            except Exception as e:
                self._json({"error": str(e)}, 400)
            return

        if url_path == "/api/upload_schema":
            params = self._parse_json(body)
            if params is None:
                return
            filename  = params.get("filename", "upload")
            b64       = params.get("content_b64", "")
            try:
                file_bytes = base64.b64decode(b64)
            except Exception:
                self._json({"error": "Invalid base64 content."}, 400)
                return

            try:
                seed_sql, table_names = import_schema(filename, file_bytes)
            except (ValueError, ImportError) as e:
                self._json({"error": str(e)}, 400)
                return
            except Exception as e:
                self._json({"error": f"Unexpected error while parsing file: {e}"}, 400)
                return

            # Derive a tab name from the filename stem
            import re as _re
            stem = filename.rsplit(".", 1)[0] if "." in filename else filename
            tab_name = _re.sub(r"[^\w]", "_", stem)[:30] or "custom"
            # Avoid name collision with built-in schemas
            base = tab_name
            idx = 2
            existing = set(list_available_schemas()) | set(uploaded_schemas.keys())
            while tab_name in existing:
                tab_name = f"{base}_{idx}"
                idx += 1

            uploaded_schemas[tab_name] = seed_sql

            # Count rows
            try:
                tmp = SQLiteAdapter(seed_sql=seed_sql)
                row_count = 0
                for tbl in tmp.get_schema().tables:
                    res = tmp.execute_readonly(f"SELECT COUNT(*) FROM \"{tbl.name}\";")
                    if res.rows:
                        row_count += res.rows[0][0]
                tmp.close()
            except Exception:
                row_count = 0

            print(f"Uploaded schema '{tab_name}': {len(table_names)} table(s)")
            self._json({
                "status":      "ok",
                "schema_name": tab_name,
                "table_names": table_names,
                "table_count": len(table_names),
                "row_count":   row_count,
            })
            return

        if url_path == "/api/query":
            params = self._parse_json(body)
            if params is None:
                return
            prompt = params.get("prompt", "")
            if not prompt:
                self._json({"error": "Missing 'prompt' parameter"}, 400)
                return

            print(f"[{current_schema_name}] {prompt}")
            out = pipeline.ask(prompt)
            inf_t = sum(v for k, v in out.timings.items() if k.startswith("generate"))
            q_t   = sum(v for k, v in out.timings.items() if k.startswith("execute"))
            self._json({
                "sql":            out.sql,
                "columns":        out.result.columns if out.result else [],
                "rows":           out.result.rows    if out.result else [],
                "truncated":      out.result.truncated if out.result else False,
                "error":          out.error,
                "attempts":       out.attempts,
                "timings":        out.timings,
                "inference_time": inf_t,
                "query_time":     q_t,
                "is_translated":  out.is_translated,
                "translated_query": out.question if out.is_translated else None,
                "original_query": out.original_question,
            })
            return

        self._send(404, "text/plain", b"Not Found")

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    def _parse_json(self, body: bytes) -> dict | None:
        try:
            return json.loads(body.decode("utf-8"))
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
    httpd = HTTPServer(("", port), DashboardHandler)
    print(f"\n{'='*52}")
    print(f"  NLP-SQL  ->  http://localhost:{port}")
    print(f"  Built-in schemas : {list_available_schemas()}")
    print(f"  Active schema    : {current_schema_name}")
    print(f"{'='*52}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")


if __name__ == "__main__":
    run_server()
