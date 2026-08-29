import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from apps.api.app.live_database import LiveBase
from apps.api.app.live_persistence import LiveMarketTickRecord


def _config(database_path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _tables(database_path: Path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        }


def _indexes(database_path: Path, table: str) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        return {
            str(row[1])
            for row in connection.execute(f"PRAGMA index_list('{table}')")
        }


def test_live_metadata_contains_no_legacy_simulation_tables() -> None:
    assert LiveMarketTickRecord.__table__.metadata is LiveBase.metadata
    assert set(LiveBase.metadata.tables) == {"live_market_ticks"}


def test_alembic_upgrade_and_downgrade_manage_only_live_schema(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "live-schema.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = _config(database_path)

    command.upgrade(config, "head")

    tables = _tables(database_path)
    assert "alembic_version" in tables
    assert "live_market_ticks" in tables
    assert "simulation_fills" not in tables

    indexes = _indexes(database_path, "live_market_ticks")
    assert "ix_live_market_ticks_symbol_received" in indexes
    assert "ix_live_market_ticks_received_at" in indexes

    command.downgrade(config, "base")

    tables_after_downgrade = _tables(database_path)
    assert "live_market_ticks" not in tables_after_downgrade
    assert "simulation_fills" not in tables_after_downgrade
