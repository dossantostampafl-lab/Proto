from datetime import UTC, datetime

import pytest

from apps.api.app.live_monitor import LiveCryptoMonitor
from services.market_data.core import MarketTick


def _tick() -> MarketTick:
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
        sequence=1,
    )


@pytest.mark.asyncio
async def test_monitor_snapshot_keeps_read_only_payload_contract() -> None:
    monitor = LiveCryptoMonitor()
    assert await monitor.ingest_tick(_tick()) is True

    payload = monitor.snapshot("BTC")

    assert payload is not None
    assert payload["source"] == "PUBLIC_READ_ONLY"
    assert payload["financial_connectivity"] is False
    assert payload["real_money_execution"] is False
    assert payload["connection_generation"] == 0
    assert payload["received_at"] is not None
