from apps.api.app.schema_registry import CANONICAL_TABLE_NAMES, CANONICAL_TABLES


def test_canonical_tables_expose_correlation_indexes() -> None:
    for name in CANONICAL_TABLE_NAMES:
        table = CANONICAL_TABLES[name]
        indexed_columns = {
            column.name
            for index in table.indexes
            for column in index.columns
        }
        assert "correlation_id" in indexed_columns
