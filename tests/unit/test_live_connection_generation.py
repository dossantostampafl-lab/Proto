from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from apps.api.app.live_monitor import LiveCryptoMonitor
from services.market_data.core import MarketTick
from services.market_data.live import PublicFeedHealth


class GenerationAdapter:
    symbols = ("BTC", "ETH", "SOL")

    def __init__(self) -> None:
        self.generation = 1

    def health(self) -> PublicFeedHealth:
        now = datetime.now(UTC)
        return PublicFeedHealth(
            connected=True,
            connection_generation=self.generation,
            connection_attempts=self.generation,
            reconnect_count=max(self.generation - 1, 0),
            frames_received=self.generation,
            ticks_emitted=self.generation,
            parse_error_count=0,
            consecutive_parse_errors=0,
            message_timeout_count=0,
            connected_since=now - timedelta(seconds=1),
            last_message_at=now,
            last_tick_at=now,
            last_error=None,
        )

    async def stream(self) -> AsyncIterator[MarketTick]:
        if False:
            yield _tick(sequence=1)


def _tick(*, sequence: int) -> MarketTick:
    return MarketTick(
        timestamp=datetime.now(UTC),
        venue="coinbase-public",
        symbol="BTC",
        bid=60_000.0,
        ask=60_001.0,
        last=60_000.5,
        volume=120.0,
        bid_size=2.0,
        ask_size=1.5,
        sequence=sequence,
    )


@pytest.mark.asyncio
async def test_live_snapshot_tracks_current_connection_generation() -> None:
    adapter = GenerationAdapter()
    monitor = LiveCryptoMonitor(adapter=adapter)

    assert await monitor.ingest_tick(_tick(sequence=1)) is True
    first = monitor.snapshot("BTC")
    assert first is not None
    assert first["connection_generation"] == 1
    assert first["sequence"] == 1

    adapter.generation = 2
    assert await monitor.ingest_tick(_tick(sequence=2)) is True
    second = monitor.snapshot("BTC")
    assert second is not None
    assert second["connection_generation"] == 2
    assert second["sequence"] == 2
