from collections.abc import AsyncIterator
from datetime import UTC, datetime

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
            yield _tick("BTC", 1)


def _tick(symbol: str, sequence: int) -> MarketTick:
    prices = {
        "BTC": (60_000.0, 60_001.0),
        "ETH": (3_000.0, 3_001.0),
        "SOL": (140.0, 140.1),
    }
    bid, ask = prices[symbol]
    return MarketTick(
        timestamp=datetime.now(UTC),
        venue="coinbase-public",
        symbol=symbol,
        bid=bid,
        ask=ask,
        last=(bid + ask) / 2.0,
        volume=1.0,
        bid_size=1.0,
        ask_size=1.0,
        sequence=sequence,
    )


@pytest.mark.asyncio
async def test_new_connection_generation_invalidates_old_cache_and_history() -> None:
    adapter = GenerationAdapter()
    monitor = LiveCryptoMonitor(adapter=adapter)

    for index, symbol in enumerate(adapter.symbols, start=1):
        assert await monitor.ingest_tick(_tick(symbol, index)) is True

    assert monitor.status()["symbols"] == ["BTC", "ETH", "SOL"]
    assert monitor.analytics("BTC") is not None

    adapter.generation = 2
    assert await monitor.ingest_tick(_tick("BTC", 1)) is True

    status = monitor.status()
    assert status["symbols"] == ["BTC"]
    assert status["missing_symbols"] == ["ETH", "SOL"]
    assert status["current_connection_symbols"] == ["BTC"]
    assert monitor.analytics("ETH") is None
    assert monitor.snapshot("ETH") is None
