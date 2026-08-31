from datetime import UTC, datetime, timedelta

import pytest

from services.events.runtime import EventRuntime
from services.market_data.core import MarketTick
from services.market_data.pipeline import MarketDataPipeline


def _tick(*, sequence: int, timestamp: datetime, bid: float = 100.0) -> MarketTick:
    return MarketTick(
        timestamp=timestamp,
        venue="public_feed",
        symbol="btc",
        bid=bid,
        ask=bid + 1.0,
        last=bid + 0.5,
        volume=10.0 + sequence,
        bid_size=5.0,
        ask_size=4.0,
        sequence=sequence,
    )


@pytest.mark.asyncio
async def test_pipeline_normalizes_builds_features_and_publishes() -> None:
    runtime = EventRuntime(backend="memory")
    await runtime.start()
    pipeline = MarketDataPipeline(event_runtime=runtime)
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)

    first = await pipeline.ingest(_tick(sequence=1, timestamp=now), received_at=now)
    second = await pipeline.ingest(
        _tick(sequence=2, timestamp=now + timedelta(seconds=1), bid=101.0),
        received_at=now + timedelta(seconds=1),
    )

    assert first.event.source == "PUBLIC_FEED"
    assert first.event.symbol == "BTC"
    assert first.quality.valid is True
    assert first.feature.sample_count == 1
    assert first.published_message_id == "1-0"
    assert second.feature.sample_count == 2
    assert second.feature.price_velocity > 0
    assert second.published_message_id == "2-0"
    assert runtime.snapshot().publish_count == 2


@pytest.mark.asyncio
async def test_duplicate_event_is_suppressed_from_bus() -> None:
    runtime = EventRuntime(backend="memory")
    await runtime.start()
    pipeline = MarketDataPipeline(event_runtime=runtime)
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    tick = _tick(sequence=7, timestamp=now)

    first = await pipeline.ingest(tick, received_at=now)
    duplicate = await pipeline.ingest(tick, received_at=now)

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.published_message_id is None
    assert runtime.snapshot().publish_count == 1


@pytest.mark.asyncio
async def test_invalid_quality_fails_closed_before_publish() -> None:
    runtime = EventRuntime(backend="memory")
    await runtime.start()
    pipeline = MarketDataPipeline(event_runtime=runtime)
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    stale = _tick(sequence=1, timestamp=now - timedelta(seconds=30))

    with pytest.raises(ValueError, match="STALE_FEED"):
        await pipeline.ingest(stale, received_at=now)

    assert runtime.snapshot().publish_count == 0


@pytest.mark.asyncio
async def test_received_at_must_be_timezone_aware() -> None:
    pipeline = MarketDataPipeline()
    aware = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="received_at must be timezone-aware"):
        await pipeline.ingest(
            _tick(sequence=1, timestamp=aware),
            received_at=datetime(2026, 8, 31, 12, 0),
        )
