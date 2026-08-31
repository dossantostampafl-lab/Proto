from __future__ import annotations

import importlib.util
from pathlib import Path

from apps.api.app.persistence import SimulationFillRecord, SimulationSessionRecord
from apps.api.app.schema_registry import CANONICAL_TABLE_NAMES


def _load_migration_module(name: str):
    path = Path(f"migrations/versions/{name}.py")
    spec = importlib.util.spec_from_file_location(f"migration_{name}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_migration_covers_registry_tables() -> None:
    migration = _load_migration_module("0002_canonical_persistence")
    assert tuple(migration.CANONICAL_TABLE_NAMES) == CANONICAL_TABLE_NAMES
    assert migration.down_revision == "0001_live_market_ticks"


def test_simulation_fill_migration_matches_runtime_model_columns() -> None:
    runtime_columns = set(SimulationFillRecord.__table__.columns.keys())
    expected = {
        "id",
        "order_id",
        "session_id",
        "market_id",
        "asset",
        "side",
        "filled_quantity",
        "fill_price",
        "fee",
        "slippage_bps",
        "filled_at",
    }
    assert runtime_columns == expected


def test_simulation_session_migration_matches_runtime_model_columns() -> None:
    migration = _load_migration_module("0003_simulation_sessions")
    assert migration.down_revision == "0002_canonical_persistence"
    assert set(SimulationSessionRecord.__table__.columns.keys()) == {
        "id",
        "created_at",
        "active",
    }


def test_alembic_tracks_all_runtime_metadata() -> None:
    source = Path("migrations/env.py").read_text(encoding="utf-8")
    assert "LiveBase.metadata" in source
    assert "Base.metadata" in source
    assert "canonical_metadata" in source
    assert "target_metadata = [" in source
