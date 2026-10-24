from core.adapters.base import Schema, Table, Column
from core.adapters.sqlite import SQLiteAdapter
from core.retriever import SchemaRetriever
from core.pipeline import Pipeline

LARGE_SCHEMA = Schema(
    tables=[
        Table(
            name="customers",
            columns=[
                Column("id", "INTEGER", is_pk=True),
                Column("customer_name", "TEXT"),
                Column("email", "TEXT"),
                Column("address", "TEXT"),
                Column("zip_code", "TEXT"),
            ],
        ),
        Table(
            name="orders",
            columns=[
                Column("id", "INTEGER", is_pk=True),
                Column("customer_id", "INTEGER"),
                Column("order_date", "TEXT"),
                Column("total_amount", "REAL"),
                Column("shipping_carrier", "TEXT"),
            ],
            foreign_keys=[("customer_id", "customers", "id")],
        ),
        Table(
            name="products",
            columns=[
                Column("id", "INTEGER", is_pk=True),
                Column("product_name", "TEXT"),
                Column("unit_price", "REAL"),
                Column("sku", "TEXT"),
            ],
        ),
        Table(
            name="suppliers",
            columns=[
                Column("id", "INTEGER", is_pk=True),
                Column("supplier_name", "TEXT"),
                Column("contact_phone", "TEXT"),
            ],
        ),
        Table(
            name="invoices",
            columns=[
                Column("id", "INTEGER", is_pk=True),
                Column("invoice_number", "TEXT"),
                Column("tax_amount", "REAL"),
            ],
        ),
        Table(
            name="reviews",
            columns=[
                Column("id", "INTEGER", is_pk=True),
                Column("rating", "INTEGER"),
                Column("comments", "TEXT"),
            ],
        ),
    ]
)


def test_schema_retriever_table_selection():
    retriever = SchemaRetriever(max_tables=2)
    pruned = retriever.retrieve(
        "Find the total amount spent by each customer on orders", LARGE_SCHEMA
    )

    table_names = {t.name for t in pruned.tables}
    assert "customers" in table_names
    assert "orders" in table_names
    # Unrelated tables should be pruned
    assert "suppliers" not in table_names
    assert "reviews" not in table_names


def test_schema_retriever_foreign_key_expansion():
    # Only "orders" is explicitly matched, but "customers" is linked via foreign key
    retriever = SchemaRetriever(max_tables=1, include_foreign_key_paths=True)
    pruned = retriever.retrieve("Show all recent orders", LARGE_SCHEMA)

    table_names = {t.name for t in pruned.tables}
    assert "orders" in table_names
    assert "customers" in table_names


def test_column_pruning_preserves_keys():
    retriever = SchemaRetriever(max_tables=2, max_cols_per_table=2)
    pruned = retriever.retrieve("What is the customer name?", LARGE_SCHEMA)

    cust_table = next(t for t in pruned.tables if t.name == "customers")
    cols = {c.name for c in cust_table.columns}

    # Primary key must be preserved even if not in question
    assert "id" in cols
    # Matching column must be preserved
    assert "customer_name" in cols


class MockRetrieverGen:
    def generate(self, question, schema_ddl, feedback=None):
        return "SELECT name FROM employees;"


def test_pipeline_with_retriever_integration():
    seed_sql = """
    CREATE TABLE employees (id INT PRIMARY KEY, name TEXT);
    INSERT INTO employees VALUES (1, 'Alice');
    """
    adapter = SQLiteAdapter(seed_sql=seed_sql)
    generator = MockRetrieverGen()
    retriever = SchemaRetriever(max_tables=2)
    pipeline = Pipeline(adapter, generator, retriever=retriever)

    result = pipeline.ask("List employee names")
    assert result.error is None
    assert "retrieve" in result.timings
    assert result.retrieved_schema is not None
    assert result.result.rows == [("Alice",)]
