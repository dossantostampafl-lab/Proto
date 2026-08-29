from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from apps.api.app.live_monitor import LiveCryptoMonitor
from services.market_data.core import MarketTick
from services.market_data.live import PublicFeedHealth


class TimingAdapter:
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
            connected_since=now - timedelta(seconds=1),
            last_message_at=now,
            last_tick_at=now,
            last_error=None,
        )

    async def stream(self) -> AsyncIterator[MarketTick]:
        if False:
            yield _tick(datetime.now(UTC))


def _tick(timestamp: datetime) -> MarketTick:
    return MarketTick(
        timestamp=timestamp,
        venue="coinbase-public",
        symbol="BTC",
        bid=60_000.0,
        ask=60_001.0,
        last=60_000.5,
        volume=100.0,
        bid_size=1.0,
        ask_size=1.0,
        sequence=1,
    )


@pytest.mark.asyncio
async def test_live_snapshot_keeps_source_time_separate_from_server_receipt_time() -> None:
    monitor = LiveCryptoMonitor(adapter=TimingAdapter())
    source_at = datetime.now(UTC) - timedelta(seconds=1)

    assert await monitor.ingest_tick(_tick(source_at)) is True
    snapshot = monitor.snapshot("BTC")

    assert snapshot is not None
    received_at = datetime.fromisoformat(str(snapshot["received_at"]))
    assert received_at.tzinfo is not None
    assert received_at >= source_at
    assert float(snapshot["source_to_server_delta_ms"]) >= 900.0
    assert snapshot["financial_connectivity"] is False
    assert snapshot["real_money_execution"] is False


@pytest.mark.asyncio
async def test_live_analytics_exposes_latest_receive_timing_without_execution_fields() -> None:
    monitor = LiveCryptoMonitor(adapter=TimingAdapter())
    source_at = datetime.now(UTC) - timedelta(milliseconds=500)

    assert await monitor.ingest_tick(_tick(source_at)) is True
    analytics = monitor.analytics("BTC")

    assert analytics is not None
    assert analytics["latest_source_at"] == source_at.isoformat()
    assert analytics["latest_received_at"] is not None
    assert float(analytics["latest_source_to_server_delta_ms"]) >= 400.0
    assert analytics["source"] == "PUBLIC_READ_ONLY_DESCRIPTIVE"
    assert analytics["financial_connectivity"] is False
    assert analytics["real_money_execution"] is False


def test_live_source_health_exposes_server_observation_clock() -> None:
    monitor = LiveCryptoMonitor(adapter=TimingAdapter())

    health = monitor.source_health()
    observed_at = datetime.fromisoformat(str(health["server_observed_at"]))

    assert observed_at.tzinfo is not None
    assert health["source"] == "PUBLIC_READ_ONLY"
    assert health["financial_connectivity"] is False
    assert health["real_money_execution"] is False
