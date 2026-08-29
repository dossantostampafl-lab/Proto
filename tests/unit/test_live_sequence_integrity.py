from datetime import UTC, datetime, timedelta

import pytest

from apps.api.app.live_monitor import LiveCryptoMonitor
from services.market_data.core import MarketTick
from services.market_data.live import PublicFeedHealth


def _tick(*, sequence: int, observed_at: datetime) -> MarketTick:
    return MarketTick(
        timestamp=observed_at,
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
            frames_received=0,
            ticks_emitted=0,
            parse_error_count=0,
            connected_since=now,
            last_message_at=now,
            last_tick_at=now,
            last_error=None,
        )


@pytest.mark.asyncio
async def test_live_monitor_rejects_duplicate_and_regressing_sequences() -> None:
    monitor = LiveCryptoMonitor()
    started_at = datetime.now(UTC)

    assert await monitor.ingest_tick(_tick(sequence=10, observed_at=started_at)) is True
    assert (
        await monitor.ingest_tick(
            _tick(sequence=10, observed_at=started_at + timedelta(milliseconds=1))
        )
        is False
    )
    assert (
        await monitor.ingest_tick(
            _tick(sequence=9, observed_at=started_at + timedelta(milliseconds=2))
        )
        is False
    )
    assert (
        await monitor.ingest_tick(
            _tick(sequence=11, observed_at=started_at + timedelta(milliseconds=3))
        )
        is True
    )

    snapshot = monitor.snapshot("BTC")
    analytics = monitor.analytics("BTC")
    status = monitor.status()

    assert snapshot is not None
    assert snapshot["sequence"] == 11
    assert analytics is not None
    assert analytics["sample_count"] == 2
    assert status["last_sequence_by_symbol"] == {"BTC": 11}
    assert status["sequence_rejections_current_connection"] == 2
    rejection_map = status["sequence_rejections_by_symbol"]
    assert isinstance(rejection_map, dict)
    assert rejection_map["BTC"] == {
        "duplicate": 1,
        "regression": 1,
        "total": 2,
    }
    assert rejection_map["ETH"] == {
        "duplicate": 0,
        "regression": 0,
        "total": 0,
    }
    assert rejection_map["SOL"] == {
        "duplicate": 0,
        "regression": 0,
        "total": 0,
    }


@pytest.mark.asyncio
async def test_sequence_rejection_telemetry_resets_on_new_connection_generation() -> None:
    adapter = GenerationAdapter()
    monitor = LiveCryptoMonitor(adapter=adapter)
    started_at = datetime.now(UTC)

    assert await monitor.ingest_tick(_tick(sequence=7, observed_at=started_at)) is True
    assert (
        await monitor.ingest_tick(
            _tick(sequence=7, observed_at=started_at + timedelta(milliseconds=1))
        )
        is False
    )
    assert monitor.status()["sequence_rejections_current_connection"] == 1

    adapter.generation = 2
    assert (
        await monitor.ingest_tick(
            _tick(sequence=1, observed_at=started_at + timedelta(milliseconds=2))
        )
        is True
    )

    status = monitor.status()
    assert status["last_sequence_by_symbol"] == {"BTC": 1}
    assert status["sequence_rejections_current_connection"] == 0
    rejection_map = status["sequence_rejections_by_symbol"]
    assert isinstance(rejection_map, dict)
    assert rejection_map["BTC"]["total"] == 0
