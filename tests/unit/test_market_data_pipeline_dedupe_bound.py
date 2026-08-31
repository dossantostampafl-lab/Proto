from datetime import UTC, datetime, timedelta

import pytest

from services.market_data.core import MarketTick
from services.market_data.pipeline import MarketDataPipeline


def _tick(sequence: int, timestamp: datetime) -> MarketTick:
    return MarketTick(
        timestamp=timestamp,
        venue="PUBLIC_FEED",
        symbol="BTC",
        bid=100.0,
        ask=101.0,
        last=100.5,
        volume=10.0 + sequence,
        bid_size=5.0,
        ask_size=4.0,
        sequence=sequence,
    )


@pytest.mark.asyncio
async def test_dedupe_state_is_bounded() -> None:
    pipeline = MarketDataPipeline(dedupe_limit=3)
    start = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)

    for sequence in range(1, 7):
        timestamp = start + timedelta(seconds=sequence)
        await pipeline.ingest(_tick(sequence, timestamp), received_at=timestamp)
        snapshot = pipeline.snapshot()
        assert snapshot.tracked_event_ids <= 3
        assert snapshot.dedupe_capacity == 3

    snapshot = pipeline.snapshot()
    assert snapshot.accepted == 6
    assert snapshot.tracked_event_ids == 3


def test_dedupe_limit_rejects_unbounded_configuration() -> None:
    with pytest.raises(ValueError, match="dedupe_limit must be at least 2"):
        MarketDataPipeline(dedupe_limit=1)
