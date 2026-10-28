"""
Schema Importer — converts uploaded files into seed SQL for in-memory SQLite.

Supported formats:
  .sql   → passed through as-is (CREATE TABLE + INSERT statements)
  .csv   → headers become columns, rows become INSERT data
  .json  → array of objects or {key: [records]} converted to a table
  .pdf   → text extracted; CREATE TABLE blocks parsed from the text
"""
import csv
import io
import json
import re


# ── Type inference helpers ────────────────────────────────────────────────────

def _infer_col_type(values: list[str]) -> str:
    """Guess INTEGER → REAL → TEXT from a list of string samples."""
    non_empty = [v.strip() for v in values if v.strip()]
    if not non_empty:
        return "TEXT"
    try:
        for v in non_empty:
            int(v)
        return "INTEGER"
    except ValueError:
        pass
    try:
        for v in non_empty:
            float(v)
        return "REAL"
    except ValueError:
        pass
    return "TEXT"


def _sql_val(v: object, col_type: str) -> str:
    """Format a Python value as a SQL literal."""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return "NULL"
    if col_type in ("INTEGER", "REAL"):
        return str(v)
    # TEXT: escape single quotes
    return "'" + str(v).replace("'", "''") + "'"


def _safe_name(name: str) -> str:
    """Make a safe SQL identifier (strip/replace problematic chars)."""
    name = re.sub(r"[^\w]", "_", name.strip())
    return name or "col"


# ── Format parsers ────────────────────────────────────────────────────────────

def parse_sql(content: str) -> tuple[str, list[str]]:
    """Return (seed_sql, table_names) from raw SQL text."""
    table_names = re.findall(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?(\w+)[\"'`]?",
        content,
        re.IGNORECASE,
    )
    return content.strip(), table_names


def parse_csv(content: str, table_name: str = "uploaded_data") -> tuple[str, list[str]]:
    """
    Auto-detect schema from CSV headers and infer column types.
    Returns (seed_sql, [table_name]).
    """
    table_name = _safe_name(table_name)
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    fieldnames = list(reader.fieldnames or [])

    if not fieldnames:
        raise ValueError("CSV has no headers — cannot determine schema.")
    if not rows:
        raise ValueError("CSV has headers but no data rows.")

    # Infer types from all column values
    col_types: dict[str, str] = {}
    for col in fieldnames:
        samples = [r.get(col, "") for r in rows]
        col_types[col] = _infer_col_type(samples)

    safe_cols = [_safe_name(c) for c in fieldnames]

    # CREATE TABLE
    col_defs = ", ".join(
        f'"{sc}" {col_types[oc]}' for sc, oc in zip(safe_cols, fieldnames)
    )
    sql_parts = [f'CREATE TABLE "{table_name}" ({col_defs});', ""]

    # INSERT rows
    for row in rows:
        vals = ", ".join(
            _sql_val(row.get(oc, ""), col_types[oc])
            for oc in fieldnames
        )
        sql_parts.append(f'INSERT INTO "{table_name}" VALUES ({vals});')

    return "\n".join(sql_parts), [table_name]


def parse_json(content: str, table_name: str = "uploaded_data") -> tuple[str, list[str]]:
    """
    Convert a JSON array (or dict-of-arrays) into seed SQL.
    Returns (seed_sql, [table_name(s)]).
    """
    data = json.loads(content)
    all_sql: list[str] = []
    all_tables: list[str] = []

    # Handle dict-of-arrays (e.g. {"employees": [...], "departments": [...]})
    if isinstance(data, dict):
        items = {k: v for k, v in data.items() if isinstance(v, list) and v and isinstance(v[0], dict)}
        if items:
            for tname, records in items.items():
                sql, tables = _json_records_to_sql(records, _safe_name(tname))
                all_sql.append(sql)
                all_tables.extend(tables)
            return "\n\n".join(all_sql), all_tables
        raise ValueError("JSON object must contain at least one key with an array of objects.")

    if not isinstance(data, list):
        raise ValueError("JSON must be an array of objects or a dict of arrays.")
    if not data:
        raise ValueError("JSON array is empty.")
    if not isinstance(data[0], dict):
        raise ValueError("JSON array elements must be objects (dicts).")

    return _json_records_to_sql(data, _safe_name(table_name))


def _json_records_to_sql(records: list[dict], table_name: str) -> tuple[str, list[str]]:
    # Collect all keys (union across all records)
    all_keys: list[str] = []
    for rec in records:
        for k in rec:
            if k not in all_keys:
                all_keys.append(k)

    safe_keys = [_safe_name(k) for k in all_keys]

    # Infer types
    col_types: dict[str, str] = {}
    for k in all_keys:
        samples = [str(r[k]) for r in records if r.get(k) is not None]
        col_types[k] = _infer_col_type(samples)

    col_defs = ", ".join(
        f'"{sk}" {col_types[ok]}' for sk, ok in zip(safe_keys, all_keys)
    )
    sql_parts = [f'CREATE TABLE "{table_name}" ({col_defs});', ""]

    for rec in records:
        vals = ", ".join(
            _sql_val(rec.get(ok), col_types[ok]) for ok in all_keys
        )
        sql_parts.append(f'INSERT INTO "{table_name}" VALUES ({vals});')

    return "\n".join(sql_parts), [table_name]


def parse_pdf(content: bytes) -> tuple[str, list[str]]:
    """
    Extract text from a PDF and find CREATE TABLE SQL blocks within it.
    Falls back to a structured-table extractor if no SQL is found.
    """
    text = _extract_pdf_text(content)

    # Try to find SQL DDL blocks
    sql = _extract_sql_from_text(text)
    if sql:
        return parse_sql(sql)

    # Try to parse tabular data from the PDF pages
    tables_sql = _extract_pdf_tables(content)
    if tables_sql:
        all_sql = "\n\n".join(s for s, _ in tables_sql)
        all_tables = [t for _, tnames in tables_sql for t in tnames]
        return all_sql, all_tables

    raise ValueError(
        "No SQL DDL or recognisable tables found in the PDF.\n"
        "Make sure the PDF contains CREATE TABLE statements or structured tables. "
        "You can also upload a .sql, .csv, or .json file directly."
    )


def _extract_pdf_text(content: bytes) -> str:
    """Extract all text from a PDF using pdfplumber (preferred) or pypdf."""
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        return "\n".join(pages)
    except ImportError:
        pass
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ImportError:
        pass
    raise ImportError(
        "PDF parsing requires 'pdfplumber'. Install with:\n"
        "  pip install pdfplumber"
    )


def _extract_pdf_tables(content: bytes) -> list[tuple[str, list[str]]]:
    """
    Use pdfplumber's table extractor to pull grid-based tables from a PDF
    and convert them to SQL.  Returns a list of (seed_sql, [table_name]).
    """
    results: list[tuple[str, list[str]]] = []
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return results

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            for tbl_idx, table in enumerate(tables):
                if not table or len(table) < 2:
                    continue
                # First row = headers
                headers = [str(h).strip() if h else f"col{i}" for i, h in enumerate(table[0])]
                rows = table[1:]
                table_name = f"table_p{page_num}_{tbl_idx + 1}"
                # Build a CSV-like structure
                csv_text = ",".join(headers) + "\n"
                for row in rows:
                    csv_text += ",".join(
                        '"' + (str(c or "")).replace('"', '""') + '"' for c in row
                    ) + "\n"
                try:
                    sql, names = parse_csv(csv_text, table_name)
                    results.append((sql, names))
                except Exception:
                    continue
    return results


def _extract_sql_from_text(text: str) -> str:
    """
    Pull CREATE TABLE ... ; blocks from freeform text.
    Handles multi-line statements and normalises whitespace.
    """
    # Normalise line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Match CREATE TABLE blocks ending with ;
    pattern = re.compile(
        r"(CREATE\s+TABLE\b.*?;)",
        re.IGNORECASE | re.DOTALL,
    )
    matches = pattern.findall(text)

    # Also look for INSERT INTO blocks
    insert_pattern = re.compile(
        r"(INSERT\s+INTO\b.*?;)",
        re.IGNORECASE | re.DOTALL,
    )
    inserts = insert_pattern.findall(text)

    all_statements = matches + inserts
    if not all_statements:
        return ""

    return "\n\n".join(stmt.strip() for stmt in all_statements)


# ── Main dispatcher ───────────────────────────────────────────────────────────

def import_schema(
    filename: str,
    content_bytes: bytes,
    custom_table_name: str | None = None,
) -> tuple[str, list[str]]:
    """
    Dispatch to the correct parser based on file extension.

    Returns:
        (seed_sql, list_of_table_names)

    Raises:
        ValueError  – if the file cannot be parsed
        ImportError – if a required optional library is missing
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    table_name = custom_table_name or _safe_name(stem) or "uploaded_data"

    if ext == "sql":
        return parse_sql(content_bytes.decode("utf-8", errors="replace"))

    if ext == "csv":
        return parse_csv(content_bytes.decode("utf-8", errors="replace"), table_name)

    if ext == "json":
        return parse_json(content_bytes.decode("utf-8", errors="replace"), table_name)

    if ext == "pdf":
        return parse_pdf(content_bytes)

    raise ValueError(
        f"Unsupported file type '.{ext}'. "
        "Please upload a .sql, .csv, .json, or .pdf file."
    )
