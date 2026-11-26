import base64
from pathlib import Path
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
import secrets
import threading
import time
from http.cookies import SimpleCookie
from decimal import Decimal
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.generator import SQLGenerator
from core.providers.mock import MockProvider
from core.providers.ollama import OllamaProvider
from core.loader import build_pipeline, list_available_schemas
from core.schema_importer import import_schema
from core.adapters.sqlite import SQLiteAdapter
from core.voice import transcribe_local_audio
from core.security import AccessPolicy, AuditLog
from core.feedback import FeedbackStore

MODEL_PATH = os.environ.get("MODEL_PATH", "SmolLM2-135M-text-to-sql.Q8_0.gguf")
DB_ENGINE = os.environ.get("DB_ENGINE", "sqlite").lower()
DB_PATH = os.environ.get("DB_PATH")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "company")

# LLM Provider Selection
OLLAMA_HOST = os.environ.get("OLLAMA_HOST")
if OLLAMA_HOST:
    print(f"Connecting to Ollama provider at {OLLAMA_HOST}")
    provider = OllamaProvider(
        host=OLLAMA_HOST,
        model=os.environ.get("OLLAMA_MODEL", "smollm2:135m"),
    )
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
    include_feedback=False,
)

# Uploaded custom schemas: {display_name: seed_sql}
uploaded_schemas: dict[str, str] = {}
uploaded_relationships: dict[str, list[dict[str, str]]] = {}
connected_databases: dict[str, tuple[str, str]] = {}
access_policy = AccessPolicy.from_environment()
audit_log = AuditLog()
feedback_store = FeedbackStore()
pipeline_lock = threading.RLock()
auth_sessions: dict[str, tuple[str, float]] = {}
auth_sessions_lock = threading.Lock()
write_approvals: dict[str, tuple[str, str | None, str, float]] = {}
write_approvals_lock = threading.Lock()


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
            include_feedback=False,
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


def _results_equivalent(left, right) -> bool:
    return (
        left.columns == right.columns
        and sorted(repr(tuple(row)) for row in left.rows)
        == sorted(repr(tuple(row)) for row in right.rows)
        and left.truncated == right.truncated
    )


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


UI_DIR = Path(__file__).resolve().parent / "ui"


def _load_page() -> str:
    """Assemble the single-page UI from ui/index.html, ui/studio.css and ui/studio.js."""
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    css = (UI_DIR / "studio.css").read_text(encoding="utf-8")
    js = (UI_DIR / "studio.js").read_text(encoding="utf-8")
    return html.replace("/*__CSS__*/", css).replace("/*__JS__*/", js)


HTML_PAGE = _load_page()


def _security_summary(role_name: str | None, explanation: dict | None) -> dict:
    """What access control did for this answer, for the UI's verification panel."""
    summary = {
        "enforced": bool(access_policy.enabled),
        "role": role_name or "local operator",
        "masked_columns": [],
        "row_filtered_tables": [],
    }
    if not access_policy.enabled:
        return summary
    try:
        role = access_policy.role(role_name)
    except Exception:
        return summary
    used = {name.lower() for name in (explanation or {}).get("tables", [])}
    for table, options in role.tables.items():
        if used and table.lower() not in used:
            continue
        summary["masked_columns"] += [f"{table}.{column}" for column in options.get("masked_columns", [])]
        if options.get("row_filter"):
            summary["row_filtered_tables"].append(table)
    return summary


class DashboardHandler(BaseHTTPRequestHandler):
    current_role: str | None = None

    def _authenticate(self) -> str:
        if not access_policy.enabled:
            return "local_operator"
        authorization = self.headers.get("Authorization", "")
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
        role = access_policy.role_for_token(token)
        if role:
            return role
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        morsel = cookie.get("nlpsql_session")
        if morsel:
            with auth_sessions_lock:
                session = auth_sessions.get(morsel.value)
                if session and session[1] > time.time():
                    return session[0]
                auth_sessions.pop(morsel.value, None)
        raise PermissionError("Authentication required. Sign in with an access token.")

    def _visible_schema(self):
        role = access_policy.authorize_schema(self.current_role, current_schema_name)
        return access_policy.restrict_schema(adapter.get_schema(), role)

    def _feedback_examples(self):
        return feedback_store.examples(
            current_schema_name,
            role_name=self.current_role or "local_operator",
        )

    def do_GET(self):
        global current_schema_name, current_db_path, adapter
        url_path = urllib.parse.urlparse(self.path).path

        if url_path == "/api/auth/status":
            self._json({"enabled": access_policy.enabled})
            return
        if url_path.startswith("/api/"):
            try:
                self.current_role = self._authenticate()
                access_policy.authorize_schema(self.current_role, current_schema_name)
            except PermissionError as error:
                self._json({"error": str(error)}, 401)
                return

        if url_path in ("/", "/index.html"):
            page = (
                HTML_PAGE
                .replace("'__SCHEMA__'", json.dumps(current_schema_name))
                .replace("__SUGGESTIONS__", json.dumps(SCHEMA_SUGGESTIONS))
            )
            self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))

        elif url_path == "/api/schemas":
            visible_schema = self._visible_schema()
            permitted_schemas = {
                name.lower()
                for name in access_policy.role(self.current_role).schemas
            }
            data = {
                "available_schemas": [
                    name for name in (
                        list_available_schemas()
                        + sorted(set(uploaded_schemas) | set(connected_databases))
                    )
                    if not access_policy.enabled
                    or "*" in access_policy.role(self.current_role).schemas
                    or name.lower() in permitted_schemas
                ],
                "current_schema": current_schema_name,
                "current_db_path": current_db_path,
                "uploaded_schemas": sorted(uploaded_schemas.keys()),
                "tables": [t.name for t in visible_schema.tables],
            }
            self._json(data)

        elif url_path == "/api/schema_detail":
            schema = self._visible_schema()
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
            role = access_policy.authorize_schema(self.current_role, current_schema_name)
            schema = self._visible_schema()
            if not any(table.name == table_name for table in schema.tables):
                self._json({"error": "Unknown table."}, 404)
                return
            table = next(table for table in schema.tables if table.name == table_name)
            table_options = next(
                (options for name, options in role.tables.items() if name.lower() == table_name.lower()),
                {},
            )
            sample_columns = [
                column.name for column in table.columns
                if not table_options.get("columns")
                or column.name.lower() in {name.lower() for name in table_options["columns"]}
            ]
            if not sample_columns:
                self._json({"error": "No columns are visible for this table."}, 403)
                return
            quoted_table = '"' + table_name.replace('"', '""') + '"'
            projection = ", ".join('"' + name.replace('"', '""') + '"' for name in sample_columns)
            sql = f"SELECT {projection} FROM {quoted_table} LIMIT 10;"
            try:
                sql = access_policy.enforce_query(
                    sql, schema, role, adapter.dialect(), full_schema=adapter.get_schema()
                )
                result = adapter.execute_readonly(sql)
                result = access_policy.mask_result(result, role, sql)
            except Exception as error:
                self._json({"error": str(error)}, 403)
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

        elif url_path == "/api/feedback_stats":
            self._json(feedback_store.stats(
                current_schema_name,
                role_name=self.current_role or "local_operator",
            ))

        elif url_path == "/api/security_report":
            from core.security_redteam import run_redteam_suite
            self._json(run_redteam_suite(self._visible_schema(), adapter.dialect()))

        else:
            self._send(404, "text/plain", b"Not Found")

    def do_POST(self):
        global current_schema_name, current_db_path, current_db_engine, DB_ENGINE
        global pipeline, adapter, knowledge_engine, provider, uploaded_relationships
        url_path = urllib.parse.urlparse(self.path).path
        if url_path == "/api/auth/login":
            body = self._read_body()
            if body is None:
                return
            params = self._parse_json(body)
            if params is None:
                return
            if not access_policy.enabled:
                self._json({"status": "ok", "enabled": False})
                return
            token = params.get("token")
            role = access_policy.role_for_token(token if isinstance(token, str) else None)
            if not role:
                self._json({"error": "Invalid access token."}, 401)
                return
            session = secrets.token_urlsafe(32)
            with auth_sessions_lock:
                auth_sessions[session] = (role, time.time() + 8 * 60 * 60)
            self._json(
                {"status": "ok", "role": role},
                headers={"Set-Cookie": f"nlpsql_session={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800"},
            )
            return
        if url_path.startswith("/api/"):
            try:
                self.current_role = self._authenticate()
                if url_path not in {
                    "/api/switch_schema", "/api/connect_database",
                    "/api/upload_schema", "/api/preview_upload",
                    "/api/ocr", "/api/feedback_stats", "/api/security_report",
                }:
                    access_policy.authorize_schema(self.current_role, current_schema_name)
            except PermissionError as error:
                self._json({"error": str(error)}, 401)
                return
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

        if url_path == "/api/feedback":
            params = self._parse_json(body)
            if params is None:
                return
            question = params.get("question")
            sql = params.get("sql")
            rating = params.get("rating")
            corrected_sql = params.get("corrected_sql")
            if (
                not isinstance(question, str)
                or not isinstance(sql, str)
                or not isinstance(rating, int)
                or isinstance(rating, bool)
                or rating not in {-1, 1}
            ):
                self._json({"error": "Provide question, SQL, and rating (1 or -1)."}, 400)
                return
            if corrected_sql is not None:
                if not isinstance(corrected_sql, str) or not corrected_sql.strip():
                    self._json({"error": "Corrected SQL must be a non-empty string."}, 400)
                    return
                try:
                    role = access_policy.authorize_schema(self.current_role, current_schema_name)
                    visible_schema = self._visible_schema()
                    corrected_sql = access_policy.enforce_query(
                        corrected_sql, visible_schema, role, adapter.dialect(),
                        full_schema=adapter.get_schema(),
                    )
                    from core.validator import validate
                    validate(corrected_sql, adapter.get_schema(), adapter.dialect())
                    reference_result = access_policy.mask_result(
                        adapter.execute_readonly(corrected_sql), role, corrected_sql
                    )
                except Exception as error:
                    self._json({"error": f"Corrected SQL was not accepted: {error}"}, 400)
                    return
                before_match = False
                try:
                    original_sql = access_policy.enforce_query(
                        sql, visible_schema, role, adapter.dialect(),
                        full_schema=adapter.get_schema(),
                    )
                    validate(original_sql, adapter.get_schema(), adapter.dialect())
                    original_result = access_policy.mask_result(
                        adapter.execute_readonly(original_sql), role, original_sql
                    )
                    before_match = _results_equivalent(original_result, reference_result)
                except Exception:
                    before_match = False
                from core.few_shot import FewShotExample
                scoped_examples = self._feedback_examples()
                scoped_examples.append(FewShotExample(question, corrected_sql))
                with pipeline_lock:
                    learned_result = pipeline.ask(
                        question,
                        query_guard=lambda candidate, _schema: access_policy.enforce_query(
                            candidate, visible_schema, role, adapter.dialect(),
                            full_schema=_schema,
                        ),
                        result_transform=lambda result, candidate: access_policy.mask_result(
                            result, role, candidate
                        ),
                        schema_override=visible_schema,
                        feedback_examples=scoped_examples,
                    )
                after_match = (
                    learned_result.result is not None
                    and _results_equivalent(learned_result.result, reference_result)
                )
                feedback_store.add(
                    current_schema_name, question, corrected_sql, 1, corrected=True,
                    before_match=before_match, after_match=after_match,
                    role_name=self.current_role or "local_operator",
                )
            else:
                feedback_store.add(
                    current_schema_name, question, sql, rating,
                    role_name=self.current_role or "local_operator",
                )
            audit_log.record(
                self.current_role, current_schema_name, "feedback", "accepted",
                question=question, sql=corrected_sql or sql,
            )
            self._json({
                "status": "ok",
                "stats": feedback_store.stats(
                    current_schema_name,
                    role_name=self.current_role or "local_operator",
                ),
            })
            return

        if url_path == "/api/transpile":
            params = self._parse_json(body)
            if params is None:
                return
            sql = params.get("sql")
            target = params.get("dialect")
            if not isinstance(sql, str) or target not in {"sqlite", "postgres", "mysql", "duckdb"}:
                self._json({"error": "Provide SQL and a supported target dialect."}, 400)
                return
            try:
                role = access_policy.authorize_schema(self.current_role, current_schema_name)
                visible_schema = self._visible_schema()
                guarded_sql = access_policy.enforce_query(
                    sql, visible_schema, role, adapter.dialect(),
                    full_schema=adapter.get_schema(),
                )
                from core.query_tools import transpile_sql
                translated_sql = transpile_sql(
                    guarded_sql, adapter.dialect(), target
                )
            except Exception as error:
                self._json({"error": str(error)}, 400)
                return
            self._json({"sql": translated_sql, "dialect": target})
            return

        if url_path == "/api/model_arena":
            params = self._parse_json(body)
            if params is None:
                return
            question = params.get("question")
            reference_sql = params.get("reference_sql")
            if not isinstance(question, str) or not question.strip():
                self._json({"error": "Provide a question for the model arena."}, 400)
                return
            try:
                role = access_policy.authorize_schema(self.current_role, current_schema_name)
                visible_schema = self._visible_schema()
                from core.model_arena import run_model_arena
                report = run_model_arena(
                    question,
                    adapter,
                    access_policy=access_policy,
                    role_policy=role,
                    schema=visible_schema,
                    reference_sql=reference_sql if isinstance(reference_sql, str) else None,
                )
                audit_log.record(
                    self.current_role, current_schema_name, "model_arena", "completed",
                    question=question,
                )
                self._json(report)
            except Exception as error:
                self._json({"error": str(error)}, 400)
            return

        if url_path == "/api/preview_write":
            params = self._parse_json(body)
            if params is None:
                return
            sql = params.get("sql")
            try:
                role = access_policy.authorize_schema(self.current_role, current_schema_name)
                role_policy = access_policy.role(role)
                if not isinstance(adapter, SQLiteAdapter):
                    raise PermissionError("Approval-based writes are currently limited to in-memory SQLite.")
                if not isinstance(sql, str) or not sql.strip():
                    raise ValueError("Provide one INSERT or UPDATE statement.")
                visible_schema = self._visible_schema()
                guarded_sql = access_policy.enforce_write(
                    sql, visible_schema, role_policy, adapter.dialect()
                )
                preview = adapter.approved_write(guarded_sql, commit=False)
                preview = self._mask_write_preview(preview, role_policy)
                token = secrets.token_urlsafe(24)
                with write_approvals_lock:
                    write_approvals[token] = (
                        current_schema_name, role, guarded_sql, time.time() + 120
                    )
                audit_log.record(
                    role, current_schema_name, "write_preview", "rolled_back", sql=guarded_sql
                )
                self._json({"preview": preview, "approval_token": token, "expires_in": 120})
            except Exception as error:
                audit_log.record(
                    self.current_role, current_schema_name, "write_preview", "rejected",
                    sql=sql if isinstance(sql, str) else None,
                )
                self._json({"error": str(error)}, 400)
            return

        if url_path == "/api/approve_write":
            params = self._parse_json(body)
            if params is None:
                return
            token = params.get("approval_token")
            if not isinstance(token, str):
                self._json({"error": "An approval token is required."}, 400)
                return
            with write_approvals_lock:
                approval = write_approvals.pop(token, None)
            if not approval or approval[3] < time.time():
                self._json({"error": "Approval token is invalid, expired, or already used."}, 400)
                return
            schema_name, approval_role, sql, _expires = approval
            try:
                if schema_name != current_schema_name or approval_role != self.current_role:
                    raise PermissionError("Approval is bound to the schema and role that created it.")
                role_policy = access_policy.authorize_schema(self.current_role, current_schema_name)
                access_policy.enforce_write(
                    sql, self._visible_schema(), role_policy, adapter.dialect()
                )
                if not isinstance(adapter, SQLiteAdapter):
                    raise PermissionError("Approval-based writes are limited to in-memory SQLite.")
                preview = adapter.approved_write(sql, commit=True)
                preview = self._mask_write_preview(preview, role_policy)
                if knowledge_engine:
                    knowledge_engine.value_index.clear()
                    knowledge_engine._build_value_index()
                audit_log.record(
                    self.current_role, current_schema_name, "write_approval", "committed", sql=sql
                )
                self._json({"status": "committed", "preview": preview})
            except Exception as error:
                audit_log.record(
                    self.current_role, current_schema_name, "write_approval", "rejected", sql=sql
                )
                self._json({"error": str(error)}, 400)
            return

        if url_path == "/api/switch_schema":
            params = self._parse_json(body)
            if params is None:
                return
            new_schema = params.get("schema", "company")
            new_path   = params.get("path")
            try:
                access_policy.authorize_schema(self.current_role, new_schema)
            except PermissionError as error:
                self._json({"error": str(error)}, 403)
                return

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
                    include_feedback=False,
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
            if access_policy.enabled and "*" not in access_policy.role(self.current_role).schemas:
                self._json({"error": "Database connections require a role with wildcard schema access."}, 403)
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
            try:
                access_policy.authorize_schema(self.current_role, tab_name)
            except PermissionError as error:
                self._json({"error": str(error)}, 403)
                return

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

            try:
                role = access_policy.authorize_schema(self.current_role, current_schema_name)
                visible_schema = self._visible_schema()
                conversation_context = params.get("conversation_context", "")
                if not isinstance(conversation_context, str) or len(conversation_context) > 8000:
                    self._json({"error": "Conversation context must be a string under 8 KB."}, 400)
                    return
            except (PermissionError, ValueError) as error:
                self._json({"error": str(error)}, 403)
                return
            print(f"[{current_schema_name}] query received")

            semantic_layer = getattr(
                getattr(knowledge_engine, "semantic_layer", None), "tables", {}
            )
            if semantic_layer:
                from core.clarification import find_ambiguous_terms
                layer = knowledge_engine.semantic_layer
                ambiguities = find_ambiguous_terms(prompt, layer)
                if ambiguities:
                    self._json({"clarification": ambiguities[0], "error": None})
                    return

            query_guard = lambda sql, _schema: access_policy.enforce_query(
                sql, visible_schema, role, adapter.dialect(), full_schema=_schema
            )
            result_transform = lambda result, sql: access_policy.mask_result(
                result, role, sql
            )
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

                with pipeline_lock:
                    out = pipeline.ask(
                        prompt,
                        on_progress=send_progress,
                        conversation_context=conversation_context or None,
                        query_guard=query_guard,
                        result_transform=result_transform,
                        schema_override=visible_schema,
                        feedback_examples=self._feedback_examples(),
                    )
            else:
                with pipeline_lock:
                    out = pipeline.ask(
                        prompt,
                        conversation_context=conversation_context or None,
                        query_guard=query_guard,
                        result_transform=result_transform,
                        schema_override=visible_schema,
                        feedback_examples=self._feedback_examples(),
                    )
            inf_t = sum(v for k, v in out.timings.items() if k.startswith("generate"))
            q_t   = sum(v for k, v in out.timings.items() if k.startswith("execute"))
            audit_log.record(
                self.current_role,
                current_schema_name,
                "query",
                "abstained" if out.abstained else "completed" if not out.error else "rejected",
                question=prompt,
                sql=out.sql,
            )
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
                "confidence": out.confidence,
                "confidence_agreement": out.confidence_agreement,
                "confidence_candidates": out.confidence_candidates,
                "abstained": out.abstained,
                "explanation": out.explanation,
                "narration": out.narration,
                "cost_guard": out.cost_guard,
                "feature_errors": out.feature_errors,
                "security": _security_summary(self.current_role, out.explanation),
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
                role = access_policy.authorize_schema(self.current_role, current_schema_name)
                visible_schema = self._visible_schema()
                sql = access_policy.enforce_query(
                    sql, visible_schema, role, adapter.dialect(),
                    full_schema=adapter.get_schema(),
                )
            except (PermissionError, ValidationError, ValueError) as error:
                self._json({"error": str(error)}, 403)
                return
            try:
                validate(sql, adapter.get_schema(), adapter.dialect())
                result = adapter.execute_readonly(sql)
                result = access_policy.mask_result(result, role, sql)
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

    def _json(
        self,
        data: dict,
        status: int = 200,
        headers: dict | None = None,
    ):
        self._send(
            status,
            "application/json",
            json.dumps(data, default=str).encode("utf-8"),
            headers=headers,
        )

    def _send(self, status: int, content_type: str, body: bytes, headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _mask_write_preview(preview: dict, role_policy) -> dict:
        masked_columns = {
            name.lower()
            for options in role_policy.tables.values()
            for name in options.get("masked_columns", [])
        }
        for key in ("before", "after"):
            preview[key] = [
                [
                    "***" if column.lower() in masked_columns else value
                    for column, value in zip(preview["columns"], row)
                ]
                for row in preview[key]
            ]
        return preview

    def log_message(self, *args):
        pass  # Suppress default access log


def run_server(port: int = 8000, host: str | None = None):
    host = host or os.environ.get("NLPSQL_HOST", "127.0.0.1")
    httpd = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"\n{'='*52}")
    display_host = "localhost" if host == "127.0.0.1" else host
    print(f"  NLP-SQL  ->  http://{display_host}:{port}")
    print(f"  Built-in schemas : {list_available_schemas()}")
    print(f"  Active schema    : {current_schema_name}")
    print(f"{'='*52}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run_server()
