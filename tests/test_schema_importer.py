import pytest

from core.schema_importer import import_schema


def test_csv_import_accepts_safe_column_type_overrides():
    sql, tables = import_schema(
        "employees.csv",
        b"id,name\n001,Ada\n002,Grace\n",
        type_overrides={"id": "TEXT"},
    )

    assert tables == ["employees"]
    assert '"id" TEXT' in sql
    assert "'001'" in sql


def test_csv_type_override_accepts_sanitized_header_name():
    sql, _ = import_schema(
        "employees.csv",
        b"Employee ID,name\n001,Ada\n",
        type_overrides={"Employee_ID": "TEXT"},
    )

    assert '"Employee_ID" TEXT' in sql


def test_numeric_type_override_rejects_non_numeric_values():
    with pytest.raises(ValueError, match="not valid INTEGER"):
        import_schema(
            "records.csv",
            b"id\n1); DROP TABLE employees;--\n",
            type_overrides={"id": "INTEGER"},
        )


def test_json_import_accepts_column_type_overrides():
    sql, tables = import_schema(
        "employees.json",
        b'[{"id":1,"name":"Ada"}]',
        type_overrides={"id": "TEXT"},
    )

    assert tables == ["employees"]
    assert '"id" TEXT' in sql


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"missing": "TEXT"}, "Unknown column"),
        ({"id": "BLOB"}, "Unsupported column type"),
    ],
)
def test_import_rejects_unsafe_or_unknown_type_overrides(overrides, error):
    with pytest.raises(ValueError, match=error):
        import_schema("records.csv", b"id\n1\n", type_overrides=overrides)


def test_sql_import_rejects_statements_that_can_access_or_modify_other_databases():
    with pytest.raises(ValueError, match="only CREATE TABLE/INDEX and INSERT"):
        import_schema("schema.sql", b"ATTACH DATABASE 'outside.db' AS outside;")


def test_sql_import_accepts_seed_ddl_and_rows():
    sql, tables = import_schema(
        "schema.sql",
        b"CREATE TABLE records (id INTEGER PRIMARY KEY); INSERT INTO records VALUES (1);",
    )

    assert tables == ["records"]
    assert "INSERT INTO records VALUES (1)" in sql


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE records (id TEXT CHECK(readfile('secret') IS NULL));",
        "CREATE TABLE records (id TEXT); INSERT INTO records SELECT readfile('secret');",
        "CREATE TABLE records (id TEXT); INSERT INTO records VALUES (readfile('secret'));",
    ],
)
def test_sql_import_rejects_function_execution(sql):
    with pytest.raises(ValueError):
        import_schema("schema.sql", sql.encode())
