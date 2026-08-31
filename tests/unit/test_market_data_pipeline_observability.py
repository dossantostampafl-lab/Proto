from datetime import UTC, datetime, timedelta

import pytest

from services.events.runtime import EventRuntime
from services.market_data.core import MarketTick
from services.market_data.pipeline import MarketDataPipeline


def _tick(*, sequence: int, timestamp: datetime) -> MarketTick:
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
        sequence=sequence,
    )


@pytest.mark.asyncio
async def test_pipeline_snapshot_tracks_accept_duplicate_and_publish() -> None:
    runtime = EventRuntime(backend="memory")
    await runtime.start()
    pipeline = MarketDataPipeline(event_runtime=runtime)
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    tick = _tick(sequence=1, timestamp=now)

    await pipeline.ingest(tick, received_at=now)
    await pipeline.ingest(tick, received_at=now)

    snapshot = pipeline.snapshot()
    assert snapshot.accepted == 1
    assert snapshot.duplicates == 1
    assert snapshot.quality_rejections == 0
    assert snapshot.publish_failures == 0
    assert snapshot.published == 1
    assert snapshot.tracked_event_ids == 1
    assert snapshot.tracked_markets == 1


@pytest.mark.asyncio
async def test_pipeline_snapshot_tracks_quality_rejection_and_reset() -> None:
    pipeline = MarketDataPipeline()
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="STALE_FEED"):
        await pipeline.ingest(
            _tick(sequence=1, timestamp=now - timedelta(seconds=30)),
            received_at=now,
        )

    assert pipeline.snapshot().quality_rejections == 1
    pipeline.reset()
    assert pipeline.snapshot().quality_rejections == 0
    assert pipeline.snapshot().tracked_event_ids == 0
