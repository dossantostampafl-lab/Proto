from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, inspect


def _load_migration_module(name: str):
    path = Path(f"migrations/versions/{name}.py")
    spec = importlib.util.spec_from_file_location(f"migration_{name}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_migration_upgrade_and_downgrade(monkeypatch) -> None:
    migration = _load_migration_module("0002_canonical_persistence")
    engine = create_engine("sqlite+pysqlite:///:memory:")

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()

        tables = set(inspect(connection).get_table_names())
        assert "simulation_fills" in tables
        assert set(migration.CANONICAL_TABLE_NAMES).issubset(tables)

        migration.downgrade()
        remaining = set(inspect(connection).get_table_names())
        assert "simulation_fills" not in remaining
        assert not set(migration.CANONICAL_TABLE_NAMES) & remaining


def test_simulation_session_migration_upgrade_and_downgrade(monkeypatch) -> None:
    canonical = _load_migration_module("0002_canonical_persistence")
    sessions = _load_migration_module("0003_simulation_sessions")
    engine = create_engine("sqlite+pysqlite:///:memory:")

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        monkeypatch.setattr(canonical, "op", operations)
        monkeypatch.setattr(sessions, "op", operations)
        canonical.upgrade()
        sessions.upgrade()

        inspector = inspect(connection)
        assert "simulation_sessions" in set(inspector.get_table_names())
        fill_columns = {column["name"] for column in inspector.get_columns("simulation_fills")}
        assert "session_id" in fill_columns

        sessions.downgrade()
        inspector = inspect(connection)
        assert "simulation_sessions" not in set(inspector.get_table_names())
        fill_columns = {column["name"] for column in inspector.get_columns("simulation_fills")}
        assert "session_id" not in fill_columns

        canonical.downgrade()
