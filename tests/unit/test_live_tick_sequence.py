"""Binance tick sequences (epoch milliseconds) must fit the live tick table."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import BigInteger

from apps.api.app.live_database import widen_tick_sequence
from apps.api.app.live_persistence import LiveMarketTickRecord

BINANCE_SEQUENCE = 1_760_626_536_219  # epoch ms, as the Binance feed produces


def test_the_sequence_column_holds_binance_epoch_milliseconds() -> None:
    assert BINANCE_SEQUENCE > 2**31 - 1  # what failed every Binance tick on Postgres
    assert isinstance(LiveMarketTickRecord.__table__.c.sequence.type, BigInteger)


class FakeConnection:
    def __init__(self, dialect: str, current: str | None) -> None:
        self.dialect = SimpleNamespace(name=dialect)
        self.current, self.statements = current, []

    async def execute(self, statement):
        self.statements.append(str(statement))
        return SimpleNamespace(scalar=lambda: self.current)


def test_an_existing_integer_column_is_widened_once_on_postgres_only() -> None:
    old = FakeConnection("postgresql", "integer")
    assert asyncio.run(widen_tick_sequence(old)) is True
    assert old.statements[-1] == "ALTER TABLE live_market_ticks ALTER COLUMN sequence TYPE BIGINT"
    done = FakeConnection("postgresql", "bigint")
    assert asyncio.run(widen_tick_sequence(done)) is False and len(done.statements) == 1
    sqlite = FakeConnection("sqlite", None)
    assert asyncio.run(widen_tick_sequence(sqlite)) is False and sqlite.statements == []


def test_the_migration_follows_the_promotion_scopes_revision() -> None:
    path = Path("migrations/versions/0011_live_tick_sequence_bigint.py")
    spec = importlib.util.spec_from_file_location("migration_0011", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0010_promotion_scopes"
