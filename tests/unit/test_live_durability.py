from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from apps.api.app.live_monitor import LiveCryptoMonitor
from services.market_data import (
    LiveTickJournalError,
    MarketTick,
    PersistedLiveTick,
    PublicFeedHealth,
)


class StaticAdapter:
    symbols = ("BTC", "ETH", "SOL")

    def health(self) -> PublicFeedHealth:
        now = datetime.now(UTC)
        return PublicFeedHealth(
            connected=True,
            connection_generation=1,
            connection_attempts=1,
            reconnect_count=0,
            frames_received=1,
            ticks_emitted=1,
            parse_error_count=0,
            connected_since=now,
            last_message_at=now,
            last_tick_at=now,
            last_error=None,
        )

    async def stream(self) -> AsyncIterator[MarketTick]:
        if False:
            yield _tick(sequence=1)


class RecordingJournal:
    def __init__(self, *, inserted: bool = True, fail: bool = False) -> None:
        self.inserted = inserted
        self.fail = fail
        self.rows: list[PersistedLiveTick] = []
        self.append_calls = 0

    async def append(
        self,
        tick: MarketTick,
        *,
        received_at: datetime,
        connection_generation: int,
    ) -> bool:
        self.append_calls += 1
        if self.fail:
            raise LiveTickJournalError("storage unavailable")
        if self.inserted:
            self.rows.append(
                PersistedLiveTick(
                    tick=tick,
                    received_at=received_at,
                    connection_generation=connection_generation,
                    persisted_at=received_at,
                )
            )
        return self.inserted

    async def list_recent(
        self,
        *,
        symbol: str,
        limit: int = 100,
    ) -> list[PersistedLiveTick]:
        return [row for row in reversed(self.rows) if row.tick.symbol == symbol][:limit]

    async def prune_before(self, cutoff: datetime) -> int:
        before = len(self.rows)
        self.rows = [row for row in self.rows if row.received_at >= cutoff]
        return before - len(self.rows)

    def status(self) -> dict[str, object]:
        return {
            "backend": "stub",
            "healthy": not self.fail,
            "write_healthy": not self.fail,
            "read_healthy": True,
        }


def _tick(*, sequence: int) -> MarketTick:
    return MarketTick(
        timestamp=datetime.now(UTC),
        venue="coinbase-public",
        symbol="BTC",
        bid=60_000.0,
        ask=60_001.0,
        last=60_000.5,
        volume=100.0,
        bid_size=1.0,
        ask_size=1.0,
        sequence=sequence,
    )


@pytest.mark.asyncio
async def test_required_persistence_failure_blocks_snapshot_and_history_acceptance() -> None:
    journal = RecordingJournal(fail=True)
    monitor = LiveCryptoMonitor(
        StaticAdapter(),
        journal=journal,
        persistence_required=True,
    )

    accepted = await monitor.ingest_tick(_tick(sequence=1))

    assert accepted is False
    assert monitor.snapshot("BTC") is None
    status = monitor.persistence_status()
    assert status["required"] is True
    assert status["healthy"] is False
    assert status["write_failures_current_connection"] == 1
    assert journal.append_calls == 1


@pytest.mark.asyncio
async def test_required_persistence_accepts_only_after_durable_append() -> None:
    journal = RecordingJournal()
    monitor = LiveCryptoMonitor(
        StaticAdapter(),
        journal=journal,
        persistence_required=True,
    )

    assert await monitor.ingest_tick(_tick(sequence=7)) is True

    snapshot = monitor.snapshot("BTC")
    assert snapshot is not None
    assert snapshot["sequence"] == 7
    assert journal.append_calls == 1
    assert len(journal.rows) == 1
    status = monitor.persistence_status()
    assert status["healthy"] is True
    assert status["persisted_current_connection"] == 1
    assert status["idempotent_hits_current_connection"] == 0

    persisted = await monitor.persisted_history("BTC", limit=10)
    assert persisted is not None
    assert persisted[0]["sequence"] == 7
    assert persisted[0]["source"] == "PUBLIC_READ_ONLY_PERSISTED"
    assert persisted[0]["financial_connectivity"] is False
    assert persisted[0]["real_money_execution"] is False


@pytest.mark.asyncio
async def test_idempotent_journal_hit_still_allows_already_durable_tick() -> None:
    journal = RecordingJournal(inserted=False)
    monitor = LiveCryptoMonitor(
        StaticAdapter(),
        journal=journal,
        persistence_required=True,
    )

    assert await monitor.ingest_tick(_tick(sequence=9)) is True
    assert monitor.snapshot("BTC") is not None
    status = monitor.persistence_status()
    assert status["idempotent_hits_current_connection"] == 1
    assert status["persisted_current_connection"] == 0


@pytest.mark.asyncio
async def test_required_persistence_without_journal_fails_closed() -> None:
    monitor = LiveCryptoMonitor(StaticAdapter(), persistence_required=True)

    assert await monitor.ingest_tick(_tick(sequence=11)) is False
    assert monitor.snapshot("BTC") is None
    status = monitor.persistence_status()
    assert status["configured"] is False
    assert status["required"] is True
    assert status["healthy"] is False
    assert status["last_write_error"] == "JOURNAL_NOT_CONFIGURED"
