from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, inspect


def _load_migration_module():
    path = Path("migrations/versions/0002_canonical_persistence.py")
    spec = importlib.util.spec_from_file_location("migration_0002_canonical_persistence", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_migration_upgrade_and_downgrade(monkeypatch) -> None:
    migration = _load_migration_module()
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
