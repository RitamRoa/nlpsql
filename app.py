import json
import os
import sqlite3
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from llama_cpp import Llama

# ---------------------------------------------------------
# 1. Database Setup (In-Memory SQLite with Sample Data)
# ---------------------------------------------------------
def init_db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    cursor = conn.cursor()
    
    # Create tables
    cursor.execute("""
    CREATE TABLE employees (
        id INTEGER PRIMARY KEY,
        name TEXT,
        department TEXT,
        salary INTEGER,
        hire_date TEXT
    );
    """)
    
    cursor.execute("""
    CREATE TABLE departments (
        id INTEGER PRIMARY KEY,
        name TEXT,
        manager_id INTEGER
    );
    """)
    
    cursor.execute("""
    CREATE TABLE sales (
        id INTEGER PRIMARY KEY,
        employee_id INTEGER,
        amount INTEGER,
        sale_date TEXT,
        FOREIGN KEY (employee_id) REFERENCES employees(id)
    );
    """)
    
    # Populate data
    employees = [
        (1, 'Alice Smith', 'Sales', 75000, '2023-01-15'),
        (2, 'Bob Jones', 'Engineering', 90000, '2022-06-20'),
        (3, 'Charlie Brown', 'Sales', 60000, '2024-03-10'),
        (4, 'Diana Prince', 'Engineering', 110000, '2021-11-05'),
        (5, 'Evan Wright', 'Marketing', 65000, '2023-08-25')
    ]
    cursor.executemany("INSERT INTO employees VALUES (?,?,?,?,?)", employees)
    
    departments = [
        (10, 'Sales', 1),
        (20, 'Engineering', 4),
        (30, 'Marketing', 5)
    ]
    cursor.executemany("INSERT INTO departments VALUES (?,?,?)", departments)
    
    sales = [
        (101, 1, 1500, '2024-01-10'),
        (102, 3, 800, '2024-01-15'),
        (103, 1, 2200, '2024-02-05'),
        (104, 5, 1200, '2024-02-12'),
        (105, 3, 1600, '2024-03-01'),
        (106, 1, 3000, '2024-03-15'),
        (107, 2, 0, '2024-03-20') # Bob had no sales
    ]
    cursor.executemany("INSERT INTO sales VALUES (?,?,?,?)", sales)
    
    conn.commit()
    return conn

# Database schema description to pass to LLM
SCHEMA_DESCRIPTION = """CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    name TEXT,
    department TEXT,
    salary INTEGER,
    hire_date TEXT
);

CREATE TABLE departments (
    id INTEGER PRIMARY KEY,
    name TEXT,
    manager_id INTEGER
);

CREATE TABLE sales (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER,
    amount INTEGER,
    sale_date TEXT,
    FOREIGN KEY (employee_id) REFERENCES employees(id)
);"""

# ---------------------------------------------------------
# 2. LLM Initialization (llama-cpp-python)
# ---------------------------------------------------------
MODEL_PATH = "SmolLM2-135M-text-to-sql.Q8_0.gguf"
if not os.path.exists(MODEL_PATH):
    raise FileNotFoundError(f"Model file not found: {MODEL_PATH}")

print("Loading GGUF model...")
# CPU execution since user requested fast CPU start
llm = Llama(
    model_path=MODEL_PATH,
    n_ctx=1024,
    n_threads=6, # Use 6 threads for fast CPU inference
    verbose=False
)
print("Model loaded successfully.")

# ---------------------------------------------------------
# 3. HTTP Server and API Handlers
# ---------------------------------------------------------
conn = init_db()

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Natural Language to SQL Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: rgba(22, 30, 49, 0.7);
            --border-color: rgba(255, 255, 255, 0.08);
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
            --accent: #3b82f6;
            --accent-glow: rgba(59, 130, 246, 0.35);
            --success: #10b981;
            --error: #ef4444;
            --mono-font: 'JetBrains Mono', monospace;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Outfit', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 2rem 1rem;
            background-image: radial-gradient(circle at 50% 0%, #1e293b 0%, #0b0f19 80%);
            overflow-x: hidden;
        }

        .container {
            width: 100%;
            max-width: 1100px;
            display: grid;
            grid-template-columns: 1fr;
            gap: 1.5rem;
        }

        header {
            text-align: center;
            margin-bottom: 1rem;
            animation: fadeIn 0.8s ease-out;
        }

        h1 {
            font-size: 2.5rem;
            font-weight: 800;
            background: linear-gradient(135deg, #60a5fa 0%, #3b82f6 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.5rem;
        }

        header p {
            color: var(--text-secondary);
            font-size: 1.1rem;
        }

        .grid-layout {
            display: grid;
            grid-template-columns: 1fr 2fr;
            gap: 1.5rem;
        }

        @media (max-width: 850px) {
            .grid-layout {
                grid-template-columns: 1fr;
            }
        }

        .card {
            background: var(--card-bg);
            backdrop-filter: blur(16px);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 1.5rem;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .schema-card {
            display: flex;
            flex-direction: column;
            gap: 1rem;
            height: fit-content;
        }

        .schema-title {
            font-weight: 600;
            font-size: 1.2rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 0.5rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .schema-badge {
            background-color: rgba(59, 130, 246, 0.2);
            color: #93c5fd;
            font-size: 0.75rem;
            padding: 0.25rem 0.5rem;
            border-radius: 9999px;
            font-weight: 500;
        }

        .schema-table {
            background: rgba(0, 0, 0, 0.2);
            border-radius: 8px;
            padding: 0.75rem;
            border: 1px solid rgba(255, 255, 255, 0.03);
        }

        .table-name {
            font-family: var(--mono-font);
            color: #f472b6;
            font-weight: 600;
            font-size: 0.95rem;
            margin-bottom: 0.25rem;
        }

        .column-list {
            font-family: var(--mono-font);
            font-size: 0.8rem;
            color: var(--text-secondary);
            list-style: none;
            padding-left: 0.5rem;
        }

        .column-list li::before {
            content: "• ";
            color: var(--accent);
        }

        .query-card {
            display: flex;
            flex-direction: column;
            gap: 1.25rem;
        }

        .form-group {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        label {
            font-weight: 600;
            font-size: 0.9rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .input-wrapper {
            position: relative;
            display: flex;
        }

        input[type="text"] {
            width: 100%;
            padding: 1rem 1.25rem;
            border-radius: 12px;
            background: rgba(15, 23, 42, 0.6);
            border: 1.5px solid var(--border-color);
            color: var(--text-primary);
            font-family: inherit;
            font-size: 1.05rem;
            outline: none;
            transition: all 0.3s;
            box-shadow: inset 0 2px 4px rgba(0,0,0,0.2);
        }

        input[type="text"]:focus {
            border-color: var(--accent);
            box-shadow: 0 0 15px var(--accent-glow), inset 0 2px 4px rgba(0,0,0,0.1);
        }

        button {
            padding: 1rem 1.5rem;
            border-radius: 12px;
            background: linear-gradient(135deg, var(--accent) 0%, #1d4ed8 100%);
            color: white;
            border: none;
            font-weight: 600;
            font-size: 1rem;
            cursor: pointer;
            transition: all 0.2s;
            box-shadow: 0 4px 14px 0 var(--accent-glow);
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
        }

        button:hover {
            transform: translateY(-1px);
            box-shadow: 0 6px 20px 0 var(--accent-glow);
            filter: brightness(1.1);
        }

        button:active {
            transform: translateY(1px);
        }

        button:disabled {
            background: #4b5563;
            cursor: not-allowed;
            box-shadow: none;
            transform: none;
        }

        .quick-suggestions {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin-top: 0.25rem;
        }

        .suggestion-btn {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-color);
            padding: 0.4rem 0.8rem;
            border-radius: 8px;
            color: var(--text-secondary);
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.2s;
        }

        .suggestion-btn:hover {
            background: rgba(59, 130, 246, 0.15);
            color: #93c5fd;
            border-color: var(--accent);
        }

        .results-section {
            display: none;
            flex-direction: column;
            gap: 1.5rem;
            margin-top: 1rem;
            animation: slideUp 0.4s ease-out;
        }

        .sql-box {
            background: #05070c;
            border-radius: 12px;
            padding: 1.25rem;
            border: 1px solid var(--border-color);
            position: relative;
        }

        .sql-box::before {
            content: "GENERATED SQL";
            position: absolute;
            top: -9px;
            left: 12px;
            background: #0b0f19;
            padding: 0 6px;
            font-size: 0.65rem;
            font-weight: 700;
            letter-spacing: 0.1em;
            color: var(--accent);
        }

        code {
            font-family: var(--mono-font);
            font-size: 0.95rem;
            color: #34d399;
            word-break: break-all;
            white-space: pre-wrap;
        }

        .stats-row {
            display: flex;
            gap: 1rem;
            font-size: 0.85rem;
            color: var(--text-secondary);
            padding: 0.25rem 0.5rem;
            background: rgba(255,255,255,0.02);
            border-radius: 6px;
            width: fit-content;
        }

        .stat-item span {
            color: var(--text-primary);
            font-weight: 600;
        }

        .data-table-container {
            width: 100%;
            overflow-x: auto;
            border-radius: 12px;
            border: 1px solid var(--border-color);
            background: rgba(0, 0, 0, 0.2);
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 0.95rem;
        }

        th {
            background: rgba(15, 23, 42, 0.8);
            padding: 0.75rem 1rem;
            font-weight: 600;
            color: var(--text-secondary);
            border-bottom: 2px solid var(--border-color);
        }

        td {
            padding: 0.75rem 1rem;
            border-bottom: 1px solid var(--border-color);
            color: var(--text-primary);
        }

        tr:last-child td {
            border-bottom: none;
        }

        tr:hover td {
            background: rgba(255, 255, 255, 0.02);
        }

        .error-box {
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid rgba(239, 68, 68, 0.2);
            color: #fca5a5;
            padding: 1rem;
            border-radius: 12px;
            font-family: var(--mono-font);
            font-size: 0.9rem;
        }

        .loader {
            display: none;
            width: 24px;
            height: 24px;
            border: 3px solid rgba(255,255,255,0.1);
            border-radius: 50%;
            border-top-color: white;
            animation: spin 1s ease-in-out infinite;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(-10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        @keyframes slideUp {
            from { opacity: 0; transform: translateY(15px); }
            to { opacity: 1; transform: translateY(0); }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Text to SQL Local Engine</h1>
            <p>Running SmolLM2-135M fully offline on CPU</p>
        </header>

        <div class="grid-layout">
            <!-- Left Panel: DB Schema -->
            <div class="card schema-card">
                <div class="schema-title">
                    <span>Database Schema</span>
                    <span class="schema-badge">SQLite</span>
                </div>
                
                <div class="schema-table">
                    <div class="table-name">employees</div>
                    <ul class="column-list">
                        <li>id (INTEGER, PK)</li>
                        <li>name (TEXT)</li>
                        <li>department (TEXT)</li>
                        <li>salary (INTEGER)</li>
                        <li>hire_date (TEXT)</li>
                    </ul>
                </div>

                <div class="schema-table">
                    <div class="table-name">departments</div>
                    <ul class="column-list">
                        <li>id (INTEGER, PK)</li>
                        <li>name (TEXT)</li>
                        <li>manager_id (INTEGER)</li>
                    </ul>
                </div>

                <div class="schema-table">
                    <div class="table-name">sales</div>
                    <ul class="column-list">
                        <li>id (INTEGER, PK)</li>
                        <li>employee_id (INTEGER, FK)</li>
                        <li>amount (INTEGER)</li>
                        <li>sale_date (TEXT)</li>
                    </ul>
                </div>
            </div>

            <!-- Right Panel: Query & Result -->
            <div class="card query-card">
                <div class="form-group">
                    <label for="prompt">Ask a question about the data</label>
                    <div class="input-wrapper">
                        <input type="text" id="prompt" placeholder="e.g. Find the name of the highest paid employee in Engineering" autocomplete="off">
                    </div>
                </div>

                <div class="quick-suggestions">
                    <button class="suggestion-btn" onclick="setPrompt(this.innerText)">List all employees in Sales</button>
                    <button class="suggestion-btn" onclick="setPrompt(this.innerText)">What is the average salary by department?</button>
                    <button class="suggestion-btn" onclick="setPrompt(this.innerText)">Find total sales amount for Alice Smith</button>
                    <button class="suggestion-btn" onclick="setPrompt(this.innerText)">Show sales details with employee names</button>
                </div>

                <button id="submitBtn" onclick="runQuery()">
                    <span id="btnText">Generate and Execute SQL</span>
                    <div class="loader" id="loader"></div>
                </button>

                <!-- Results Output -->
                <div class="results-section" id="resultsSection">
                    <div class="sql-box">
                        <code id="sqlCode">SELECT * FROM employees;</code>
                    </div>

                    <div class="stats-row">
                        <div class="stat-item">LLM Inference: <span id="inferenceTime">-</span></div>
                        <div class="stat-item">Query Run: <span id="queryTime">-</span></div>
                    </div>

                    <div id="tableOutput">
                        <!-- Table will be injected here -->
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        function setPrompt(text) {
            document.getElementById('prompt').value = text;
            document.getElementById('prompt').focus();
        }

        async function runQuery() {
            const promptInput = document.getElementById('prompt').value.trim();
            if (!promptInput) return;

            const submitBtn = document.getElementById('submitBtn');
            const loader = document.getElementById('loader');
            const btnText = document.getElementById('btnText');
            const resultsSection = document.getElementById('resultsSection');
            const tableOutput = document.getElementById('tableOutput');
            const sqlCode = document.getElementById('sqlCode');
            const inferenceTime = document.getElementById('inferenceTime');
            const queryTime = document.getElementById('queryTime');

            // Set loading state
            submitBtn.disabled = true;
            loader.style.display = 'block';
            btnText.style.display = 'none';

            try {
                const response = await fetch('/api/query', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ prompt: promptInput })
                });

                const data = await response.json();
                
                // Show results card
                resultsSection.style.display = 'flex';
                sqlCode.innerText = data.sql || '-- No SQL generated';
                inferenceTime.innerText = data.inference_time ? `${data.inference_time.toFixed(2)}s` : '-';
                queryTime.innerText = data.query_time ? `${data.query_time.toFixed(4)}s` : '-';

                if (data.error) {
                    tableOutput.innerHTML = `<div class="error-box"><strong>Database Error:</strong><br>${data.error}</div>`;
                } else if (data.columns && data.rows) {
                    if (data.rows.length === 0) {
                        tableOutput.innerHTML = '<div style="color: var(--text-secondary); text-align: center; padding: 1.5rem;">Query executed successfully, but returned 0 rows.</div>';
                    } else {
                        let html = '<div class="data-table-container"><table><thead><tr>';
                        data.columns.forEach(col => {
                            html += `<th>${col}</th>`;
                        });
                        html += '</tr></thead><tbody>';
                        data.rows.forEach(row => {
                            html += '<tr>';
                            row.forEach(val => {
                                html += `<td>${val !== null ? val : '<em>NULL</em>'}</td>`;
                            });
                            html += '</tr>';
                        });
                        html += '</tbody></table></div>';
                        tableOutput.innerHTML = html;
                    }
                }
            } catch (err) {
                tableOutput.innerHTML = `<div class="error-box"><strong>Network/Server Error:</strong><br>${err.message}</div>`;
                resultsSection.style.display = 'flex';
            } finally {
                submitBtn.disabled = false;
                loader.style.display = 'none';
                btnText.style.display = 'block';
            }
        }

        // Trigger on Enter
        document.getElementById('prompt').addEventListener('keypress', function (e) {
            if (e.key === 'Enter') {
                runQuery();
            }
        });
    </script>
</body>
</html>
"""

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        url_path = urllib.parse.urlparse(self.path).path
        if url_path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        url_path = urllib.parse.urlparse(self.path).path
        if url_path == "/api/query":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                params = json.loads(post_data.decode("utf-8"))
            except Exception:
                self.send_error_response("Invalid JSON")
                return
            
            prompt = params.get("prompt", "")
            if not prompt:
                self.send_error_response("Missing 'prompt' parameter")
                return

            print(f"\nUser Prompt: {prompt}")
            
            # Format standard chat template for SmolLM2 text-to-sql
            chat_prompt = (
                "<|im_start|>system\n"
                "You are a helpful assistant who answers questions about database tables by responding with SQL queries.<|im_end|>\n"
                "<|im_start|>user\n"
                "Given the following database schema:\n"
                f"{SCHEMA_DESCRIPTION}\n\n"
                "Answer the following question with a SQL query:\n"
                f"{prompt}<|im_end|>\n"
                "<|im_start|>assistant\n"
            )
            
            # Start LLM Generation timing
            t0 = time.time()
            output = llm(
                chat_prompt,
                max_tokens=150,
                stop=["<|im_end|>", ";", "\n\n"],
                temperature=0.0, # Greedy search for precise SQL structure
            )
            t1 = time.time()
            inference_time = t1 - t0
            
            generated_text = output["choices"][0]["text"].strip()
            print(f"Generated text: {generated_text}")
            
            # Extract SQL query (clean up common model formatting)
            sql_query = clean_sql(generated_text)
            print(f"Cleaned SQL Query: {sql_query}")
            
            # Run query in SQLite
            db_error = None
            rows = []
            columns = []
            q_time = 0.0
            
            if sql_query:
                t_q0 = time.time()
                try:
                    cursor = conn.cursor()
                    cursor.execute(sql_query)
                    rows = cursor.fetchall()
                    if cursor.description:
                        columns = [desc[0] for desc in cursor.description]
                except Exception as e:
                    db_error = str(e)
                t_q1 = time.time()
                q_time = t_q1 - t_q0
            else:
                db_error = "Could not parse SQL query from model output."

            # Send response
            response_data = {
                "sql": sql_query,
                "rows": rows,
                "columns": columns,
                "error": db_error,
                "inference_time": inference_time,
                "query_time": q_time
            }
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response_data).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def send_error_response(self, msg):
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": msg}).encode("utf-8"))

def clean_sql(text):
    # Remove markdown code blocks if the model wrapped it
    text = text.replace("```sql", "").replace("```", "")
    # Find SELECT statements or basic query indicators
    # Since SmolLM2-135M SQL is usually directly written, clean basic whitespaces
    text = text.strip()
    # Add back semicolon if missing (helps with SQLite but not required)
    if text and not text.endswith(";"):
        text += ";"
    return text

def run_server():
    server_address = ("", 8000)
    httpd = HTTPServer(server_address, DashboardHandler)
    print("Dashboard listening on http://localhost:8000")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        conn.close()

if __name__ == "__main__":
    run_server()
