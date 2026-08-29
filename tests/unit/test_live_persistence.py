from datetime import UTC, datetime, timedelta

import pytest

from apps.api.app.live_persistence import AsyncSqlLiveTickJournal
from apps.api.app.persistence import build_engine, init_database
from services.market_data import MarketTick


def _tick(*, sequence: int, observed_at: datetime) -> MarketTick:
    return MarketTick(
        timestamp=observed_at,
        venue="coinbase-public",
        symbol="BTC",
        bid=60_000.0,
        ask=60_001.0,
        last=60_000.5,
        volume=125.0,
        bid_size=2.0,
        ask_size=1.0,
        sequence=sequence,
    )


@pytest.mark.asyncio
async def test_live_sql_journal_is_idempotent_and_restart_queryable() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await init_database(engine)
    journal = AsyncSqlLiveTickJournal(engine, prune_every_writes=100)
    observed_at = datetime.now(UTC)
    tick = _tick(sequence=42, observed_at=observed_at)

    try:
        assert (
            await journal.append(
                tick,
                received_at=observed_at + timedelta(milliseconds=5),
                connection_generation=3,
            )
            is True
        )
        assert (
            await journal.append(
                tick,
                received_at=observed_at + timedelta(milliseconds=6),
                connection_generation=3,
            )
            is False
        )

        restarted_journal = AsyncSqlLiveTickJournal(engine, prune_every_writes=100)
        rows = await restarted_journal.list_recent(symbol="BTC", limit=10)

        assert len(rows) == 1
        assert rows[0].tick.sequence == 42
        assert rows[0].connection_generation == 3
        assert rows[0].tick.timestamp.tzinfo is not None
        assert rows[0].received_at.tzinfo is not None
        payload = rows[0].as_dict()
        assert payload["source"] == "PUBLIC_READ_ONLY_PERSISTED"
        assert payload["financial_connectivity"] is False
        assert payload["real_money_execution"] is False

        status = journal.status()
        assert status["writes_attempted"] == 2
        assert status["writes_inserted"] == 1
        assert status["idempotent_hits"] == 1
        assert status["write_failures"] == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_sql_journal_prunes_expired_rows_during_write_maintenance() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await init_database(engine)
    journal = AsyncSqlLiveTickJournal(
        engine,
        retention_seconds=300,
        prune_every_writes=1,
    )
    now = datetime.now(UTC)
    old_at = now - timedelta(hours=1)

    try:
        assert (
            await journal.append(
                _tick(sequence=1, observed_at=old_at),
                received_at=old_at,
                connection_generation=1,
            )
            is True
        )
        assert (
            await journal.append(
                _tick(sequence=2, observed_at=now),
                received_at=now,
                connection_generation=1,
            )
            is True
        )

        rows = await journal.list_recent(symbol="BTC", limit=10)
        assert [row.tick.sequence for row in rows] == [2]
        assert journal.status()["pruned_rows"] >= 1
        assert journal.status()["maintenance_healthy"] is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_sql_journal_explicit_prune_is_bounded_and_query_limit_applies() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await init_database(engine)
    journal = AsyncSqlLiveTickJournal(engine, prune_every_writes=100)
    now = datetime.now(UTC)

    try:
        for sequence in range(1, 4):
            observed_at = now + timedelta(milliseconds=sequence)
            assert (
                await journal.append(
                    _tick(sequence=sequence, observed_at=observed_at),
                    received_at=observed_at,
                    connection_generation=1,
                )
                is True
            )

        limited = await journal.list_recent(symbol="BTC", limit=2)
        assert [row.tick.sequence for row in limited] == [3, 2]

        deleted = await journal.prune_before(now + timedelta(milliseconds=2, microseconds=500))
        assert deleted == 2
        remaining = await journal.list_recent(symbol="BTC", limit=10)
        assert [row.tick.sequence for row in remaining] == [3]
    finally:
        await engine.dispose()
