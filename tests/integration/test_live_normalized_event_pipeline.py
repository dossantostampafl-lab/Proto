from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from apps.api.app.live_monitor import LiveCryptoMonitor
from services.events.runtime import EventRuntime
from services.market_data import MarketTick, PublicFeedHealth


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
async def test_live_read_only_tick_reaches_normalized_feature_event_bus() -> None:
    runtime = EventRuntime(backend="memory")
    await runtime.start()
    monitor = LiveCryptoMonitor(
        adapter=StaticAdapter(),
        normalized_event_runtime=runtime,
    )
    tick = _tick()

    assert await monitor.ingest_tick(tick) is True

    status = monitor.status()
    normalized = status["normalized_pipeline"]
    assert normalized["accepted"] == 1
    assert normalized["published"] == 1
    assert normalized["quality_rejections"] == 0
    assert normalized["publish_failures"] == 0
    assert runtime.snapshot().publish_count == 1
    assert monitor.snapshot("BTC") is not None

    assert await monitor.ingest_tick(tick) is False
    assert runtime.snapshot().publish_count == 1

    await runtime.close()
