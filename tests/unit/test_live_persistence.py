from datetime import UTC, datetime, timedelta

import pytest

from apps.api.app.live_database import build_live_engine, init_live_database
from apps.api.app.live_persistence import AsyncSqlLiveTickJournal
from services.market_data import LiveHistoryCursorError, MarketTick


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
    engine = build_live_engine("sqlite+aiosqlite:///:memory:")
    await init_live_database(engine)
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
    engine = build_live_engine("sqlite+aiosqlite:///:memory:")
    await init_live_database(engine)
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
    engine = build_live_engine("sqlite+aiosqlite:///:memory:")
    await init_live_database(engine)
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


@pytest.mark.asyncio
async def test_live_history_cursor_paginates_without_duplicates_or_gaps() -> None:
    engine = build_live_engine("sqlite+aiosqlite:///:memory:")
    await init_live_database(engine)
    journal = AsyncSqlLiveTickJournal(engine, prune_every_writes=100)
    now = datetime.now(UTC)

    try:
        for sequence in range(1, 6):
            observed_at = now + timedelta(seconds=sequence)
            assert await journal.append(
                _tick(sequence=sequence, observed_at=observed_at),
                received_at=observed_at,
                connection_generation=1,
            )

        first = await journal.list_page(symbol="BTC", limit=2)
        second = await journal.list_page(
            symbol="BTC",
            limit=2,
            cursor=first.next_cursor,
        )
        third = await journal.list_page(
            symbol="BTC",
            limit=2,
            cursor=second.next_cursor,
        )

        assert [row.tick.sequence for row in first.items] == [5, 4]
        assert [row.tick.sequence for row in second.items] == [3, 2]
        assert [row.tick.sequence for row in third.items] == [1]
        assert first.next_cursor is not None
        assert second.next_cursor is not None
        assert third.next_cursor is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_history_page_applies_timezone_aware_time_bounds() -> None:
    engine = build_live_engine("sqlite+aiosqlite:///:memory:")
    await init_live_database(engine)
    journal = AsyncSqlLiveTickJournal(engine, prune_every_writes=100)
    now = datetime.now(UTC)

    try:
        for sequence in range(1, 6):
            observed_at = now + timedelta(seconds=sequence)
            assert await journal.append(
                _tick(sequence=sequence, observed_at=observed_at),
                received_at=observed_at,
                connection_generation=1,
            )

        page = await journal.list_page(
            symbol="BTC",
            limit=10,
            start_at=now + timedelta(seconds=2),
            end_at=now + timedelta(seconds=4),
        )

        assert [row.tick.sequence for row in page.items] == [4, 3, 2]
        assert page.next_cursor is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_history_cursor_rejects_malformed_input() -> None:
    engine = build_live_engine("sqlite+aiosqlite:///:memory:")
    await init_live_database(engine)
    journal = AsyncSqlLiveTickJournal(engine, prune_every_writes=100)

    try:
        with pytest.raises(LiveHistoryCursorError):
            await journal.list_page(symbol="BTC", limit=10, cursor="not-a-valid-cursor!!")
    finally:
        await engine.dispose()
