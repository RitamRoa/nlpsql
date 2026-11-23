"""MCP stdio server exposing the validated NLPSQL read-only pipeline."""
import json
import os
import sys

from .loader import build_pipeline
from .providers.factory import configured_provider
from .security import AccessPolicy, AuditLog
from .validator import ValidationError


PROTOCOL_VERSION = "2024-11-05"


def _response(message_id, result=None, error=None) -> dict:
    response = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        response["error"] = error
    else:
        response["result"] = result
    return response


def _server_info() -> dict:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "nlpsql", "version": "1.0.0"},
    }


def _tools() -> list[dict]:
    return [{
        "name": "nlpsql_query",
        "description": "Answer a natural-language database question using NLPSQL's validated, read-only SQL pipeline.",
        "inputSchema": {
            "type": "object",
            "properties": {"question": {"type": "string", "minLength": 1}},
            "required": ["question"],
            "additionalProperties": False,
        },
    }]


def _run_tool(arguments: dict, pipeline, adapter, access: AccessPolicy, role_name: str, schema_name: str) -> dict:
    question = arguments.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Provide a non-empty question.")
    role = access.authorize_schema(role_name, schema_name)
    visible_schema = access.restrict_schema(adapter.get_schema(), role)
    if not visible_schema.tables:
        raise PermissionError("This role has no visible tables in the active schema.")
    audit = AuditLog()
    try:
        result = pipeline.ask(
            question,
            query_guard=lambda sql, _schema: access.enforce_query(
                sql, visible_schema, role, adapter.dialect(), full_schema=_schema
            ),
            result_transform=lambda rows, sql: access.mask_result(rows, role, sql),
            schema_override=visible_schema,
        )
        audit.record(
            role_name, schema_name, "mcp_query",
            "abstained" if result.abstained else "completed" if not result.error else "rejected",
            question=question, sql=result.sql,
        )
        return {
            "question": question,
            "sql": result.sql,
            "columns": result.result.columns if result.result else [],
            "rows": [list(row) for row in result.result.rows] if result.result else [],
            "confidence": result.confidence,
            "abstained": result.abstained,
            "error": result.error,
        }
    except (ValueError, PermissionError, ValidationError) as error:
        audit.record(role_name, schema_name, "mcp_query", "rejected", question=question)
        raise error


def serve() -> None:
    schema_name = os.environ.get("DB_SCHEMA", "company")
    engine = os.environ.get("DB_ENGINE", "sqlite")
    db_path = os.environ.get("DB_PATH")
    access = AccessPolicy.from_environment()
    role_name = os.environ.get("NLPSQL_MCP_ROLE")
    pipeline, adapter, _knowledge = build_pipeline(
        db_engine=engine,
        db_path=db_path,
        schema_name=schema_name,
        provider=configured_provider(),
    )
    try:
        for line in sys.stdin:
            message = {}
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("Expected a JSON-RPC object.")
                method = message.get("method")
                message_id = message.get("id")
                params = message.get("params") or {}
                if method == "notifications/initialized":
                    continue
                if method == "initialize":
                    result = _server_info()
                elif method == "ping":
                    result = {}
                elif method == "tools/list":
                    result = {"tools": _tools()}
                elif method == "tools/call" and params.get("name") == "nlpsql_query":
                    try:
                        data = _run_tool(
                            params.get("arguments", {}),
                            pipeline,
                            adapter,
                            access,
                            role_name,
                            schema_name,
                        )
                        result = {
                            "content": [{"type": "text", "text": json.dumps(data, default=str)}],
                            "isError": bool(data["error"]),
                        }
                    except (ValueError, PermissionError, ValidationError) as error:
                        result = {
                            "content": [{"type": "text", "text": str(error)}],
                            "isError": True,
                        }
                else:
                    if message_id is None:
                        continue
                    output = _response(
                        message_id,
                        error={"code": -32601, "message": f"Unknown method: {method}"},
                    )
                    sys.stdout.write(json.dumps(output) + "\n")
                    sys.stdout.flush()
                    continue

                if message_id is not None:
                    sys.stdout.write(json.dumps(_response(message_id, result=result), default=str) + "\n")
                    sys.stdout.flush()
            except (json.JSONDecodeError, ValueError) as error:
                message_id = message.get("id") if isinstance(message, dict) else None
                if message_id is not None:
                    sys.stdout.write(json.dumps(_response(
                        message_id,
                        error={"code": -32600, "message": str(error)},
                    )) + "\n")
                    sys.stdout.flush()
    finally:
        adapter.close()


if __name__ == "__main__":
    serve()
