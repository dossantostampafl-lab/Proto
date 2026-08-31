from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime

import pytest

from apps.api.app.live_monitor import LiveCryptoMonitor
from services.market_data import (
    LiveTickJournalError,
    MarketTick,
    PersistedLiveTick,
    PersistedLiveTickPage,
    PublicFeedHealth,
)


class StaticAdapter:
    @property
    def symbols(self) -> tuple[str, ...]:
        return ("BTC",)

    def health(self) -> PublicFeedHealth:
        now = datetime.now(UTC)
        return PublicFeedHealth(
            connected=True,
            connection_generation=1,
            connection_attempts=1,
            reconnect_count=0,
            frames_received=0,
            ticks_emitted=0,
            parse_error_count=0,
            connected_since=now,
            last_message_at=now,
            last_tick_at=now,
            last_error=None,
        )

    async def stream(self) -> AsyncIterator[MarketTick]:
        if False:
            yield _tick()


class FailOnceJournal:
    def __init__(self) -> None:
        self.calls = 0

    async def append(
        self,
        tick: MarketTick,
        *,
        received_at: datetime,
        connection_generation: int,
    ) -> bool:
        del tick, received_at, connection_generation
        self.calls += 1
        if self.calls == 1:
            raise LiveTickJournalError("temporary write failure")
        return True

    async def list_recent(
        self,
        *,
        symbol: str,
        limit: int = 100,
    ) -> Sequence[PersistedLiveTick]:
        del symbol, limit
        return ()

    async def list_page(
        self,
        *,
        symbol: str,
        limit: int = 100,
        cursor: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> PersistedLiveTickPage:
        del symbol, limit, cursor, start_at, end_at
        return PersistedLiveTickPage(items=(), next_cursor=None)

    async def prune_before(self, cutoff: datetime) -> int:
        del cutoff
        return 0

    def status(self) -> Mapping[str, object]:
        return {"write_healthy": True, "read_healthy": True}


def _tick() -> MarketTick:
    now = datetime.now(UTC)
    return MarketTick(
        timestamp=now,
        venue="PUBLIC_FEED",
        symbol="BTC",
        bid=100.0,
        ask=101.0,
        last=100.5,
        volume=10.0,
        bid_size=5.0,
        ask_size=4.0,
        sequence=1,
    )


@pytest.mark.asyncio
async def test_live_tick_retry_is_not_poisoned_by_required_persistence_failure() -> None:
    journal = FailOnceJournal()
    monitor = LiveCryptoMonitor(
        adapter=StaticAdapter(),
        journal=journal,
        persistence_required=True,
    )
    tick = _tick()

    assert await monitor.ingest_tick(tick) is False
    assert monitor.snapshot("BTC") is None
    assert monitor.status()["last_sequence_by_symbol"] == {}

    assert await monitor.ingest_tick(tick) is True
    accepted = monitor.snapshot("BTC")
    assert accepted is not None
    assert accepted["sequence"] == 1
    assert journal.calls == 2
    assert monitor.status()["sequence_rejections_current_connection"] == 0
