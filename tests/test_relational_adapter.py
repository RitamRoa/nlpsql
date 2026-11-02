import pytest

from core.adapters.relational import RelationalAdapter


@pytest.mark.parametrize(
    ("connection_string", "dialect"),
    [
        ("postgresql://demo:secret@localhost/example", "postgres"),
        ("mysql://demo:secret@localhost/example", "mysql"),
    ],
)
def test_relational_adapter_normalizes_connection_urls_without_connecting(
    connection_string, dialect
):
    adapter = RelationalAdapter(connection_string)
    try:
        assert adapter.dialect() == dialect
        assert "secret" not in str(adapter.engine.url)
        assert "secret" not in adapter._redact(f"failed for {connection_string}")
    finally:
        adapter.close()


def test_relational_adapter_rejects_unconfigured_database_schemes():
    with pytest.raises(ValueError, match="PostgreSQL or MySQL"):
        RelationalAdapter("sqlite:///not-supported.db")
