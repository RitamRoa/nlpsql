import os
import sqlite3
import tempfile
import pytest
from core.loader import (
    list_available_schemas,
    load_schema_sql,
    create_adapter,
    build_pipeline,
)


def test_list_available_schemas():
    schemas = list_available_schemas()
    assert "company" in schemas
    assert "ecommerce" in schemas
    assert "university" in schemas
    assert "healthcare" in schemas


def test_load_ecommerce_schema():
    adapter = create_adapter(db_engine="sqlite", schema_name="ecommerce")
    schema = adapter.get_schema()
    table_names = {t.name for t in schema.tables}
    assert "customers" in table_names
    assert "products" in table_names
    assert "orders" in table_names
    assert "order_items" in table_names

    res = adapter.execute_readonly("SELECT name, price FROM products WHERE price > 200;")
    assert len(res.rows) >= 2


def test_load_university_schema():
    adapter = create_adapter(db_engine="sqlite", schema_name="university")
    schema = adapter.get_schema()
    table_names = {t.name for t in schema.tables}
    assert "instructors" in table_names
    assert "courses" in table_names
    assert "enrollments" in table_names


def test_connect_to_physical_db_file_on_disk():
    # Create a real, physical .db file on disk (Option 3)
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        # Seed the disk DB
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE inventory (item_id INT PRIMARY KEY, item_name TEXT, qty INT);")
        conn.execute("INSERT INTO inventory VALUES (1, 'Widgets', 450);")
        conn.commit()
        conn.close()

        # Connect adapter using db_path (Option 3)
        adapter = create_adapter(db_engine="sqlite", db_path=db_path)
        try:
            schema = adapter.get_schema()
            assert len(schema.tables) == 1
            assert schema.tables[0].name == "inventory"

            res = adapter.execute_readonly("SELECT item_name, qty FROM inventory;")
            assert res.rows == [("Widgets", 450)]

            # Verify read-only enforcement on disk DB
            with pytest.raises(Exception):
                adapter.execute_readonly("DROP TABLE inventory;")
        finally:
            adapter.close()  # Release connection before deleting file (Windows)
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_build_pipeline_with_domain():
    pipeline, adapter, knowledge = build_pipeline(
        db_engine="sqlite", schema_name="ecommerce"
    )
    res = pipeline.ask("List all products")
    assert res.error is None
    assert res.result is not None
