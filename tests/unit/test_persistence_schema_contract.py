from apps.api.app.persistence import Base
from apps.api.app.schema_registry import CANONICAL_TABLE_NAMES, canonical_metadata


def test_paper_fill_table_is_registered() -> None:
    assert "simulation_fills" in Base.metadata.tables


def test_canonical_registry_contains_required_tables() -> None:
    assert tuple(canonical_metadata.tables) == CANONICAL_TABLE_NAMES
