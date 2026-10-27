from __future__ import annotations

import importlib.util
from pathlib import Path

from apps.api.app.persistence import SimulationFillRecord, SimulationSessionRecord
from apps.api.app.schema_registry import (
    BASE_CANONICAL_TABLE_NAMES,
    CANONICAL_TABLE_NAMES,
)


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
    assert tuple(migration.CANONICAL_TABLE_NAMES) == BASE_CANONICAL_TABLE_NAMES
    assert migration.down_revision == "0001_live_market_ticks"


def test_research_experiment_migration_extends_registry() -> None:
    migration = _load_migration_module("0005_research_experiments")
    assert migration.down_revision == "0004_session_scoped_fill_idempotency"
    assert migration.TABLE_NAME == "research_experiments"
    assert CANONICAL_TABLE_NAMES == (
        *BASE_CANONICAL_TABLE_NAMES,
        migration.TABLE_NAME,
    )


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
    assert "OrchestrationBase.metadata" in source
    assert "DecisionMemoryBase.metadata" in source
    assert "target_metadata = [" in source


def test_alembic_head_creates_every_table_the_runtime_creates(tmp_path, monkeypatch) -> None:
    """A database built only by `alembic upgrade head` has every table `init_database` and
    `init_control_database` create (decision_memory had no migration before 0015)."""

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from apps.api.app import persistence
    from services.orchestration import DecisionMemoryBase, OrchestrationBase

    database = tmp_path / "head.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    command.upgrade(Config("alembic.ini"), "head")

    runtime_tables = {
        table
        for metadata in (
            *persistence.CONTROL_PLANE_METADATA,
            persistence.Base.metadata,
            persistence.canonical_metadata,
            OrchestrationBase.metadata,
            DecisionMemoryBase.metadata,
        )
        for table in metadata.tables
    }
    engine = create_engine(f"sqlite:///{database}")
    try:
        migrated = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert runtime_tables <= migrated, sorted(runtime_tables - migrated)


def test_decision_memory_migration_keeps_a_table_the_runtime_already_created(
    monkeypatch,
) -> None:
    migration = _load_migration_module("0015_decision_memory")
    assert migration.down_revision == "0014_agent_conversations"

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect

    from services.orchestration import DecisionMemoryBase

    table = DecisionMemoryBase.metadata.tables["decision_memory"]
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        table.create(connection)
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()  # no-op: the runtime already created it
        migration.downgrade()
        assert "decision_memory" not in inspect(connection).get_table_names()
        migration.upgrade()
        columns = {c["name"] for c in inspect(connection).get_columns("decision_memory")}
        assert columns == set(table.columns.keys())
