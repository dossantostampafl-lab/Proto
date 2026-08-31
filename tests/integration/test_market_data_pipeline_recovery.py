from datetime import UTC, datetime

import pytest

from services.events.runtime import EventRuntime
from services.market_data.core import MarketTick
from services.market_data.pipeline import MarketDataPipeline


def _tick(timestamp: datetime) -> MarketTick:
    return MarketTick(
        timestamp=timestamp,
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
async def test_event_can_be_retried_after_bus_recovers_without_false_duplicate() -> None:
    runtime = EventRuntime(backend="memory")
    pipeline = MarketDataPipeline(event_runtime=runtime)
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    tick = _tick(now)

    with pytest.raises(RuntimeError, match="event runtime is not ready"):
        await pipeline.ingest(tick, received_at=now)

    failed = pipeline.snapshot()
    assert failed.accepted == 0
    assert failed.published == 0
    assert failed.publish_failures == 1
    assert failed.tracked_event_ids == 0
    assert failed.tracked_markets == 0

    await runtime.start()
    retried = await pipeline.ingest(tick, received_at=now)

    assert retried.duplicate is False
    assert retried.published_message_id == "1-0"
    recovered = pipeline.snapshot()
    assert recovered.accepted == 1
    assert recovered.published == 1
    assert recovered.publish_failures == 1
    assert recovered.tracked_event_ids == 1
    assert recovered.tracked_markets == 1
