from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api.app.live_monitor import LiveCryptoMonitor
from apps.api.app.main import app
from services.market_data.core import MarketTick

client = TestClient(app)


def _tick(*, observed_at: datetime | None = None, sequence: int = 1) -> MarketTick:
    return MarketTick(
        timestamp=observed_at or datetime.now(UTC),
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


def test_live_routes_are_mounted_with_financial_connectivity_disabled() -> None:
    response = client.get("/live/status")
    body = response.json()

    assert response.status_code == 200
    assert body["mode"] == "LIVE_MONITORING"
    assert body["source"] == "PUBLIC_READ_ONLY"
    assert body["financial_connectivity"] is False
    assert body["real_money_execution"] is False


def test_live_readiness_fails_closed_without_fresh_frames() -> None:
    response = client.get("/live/ready")
    body = response.json()

    assert response.status_code == 503
    assert body["status"] == "not_ready"
    assert body["financial_connectivity"] is False
    assert body["real_money_execution"] is False


@pytest.mark.asyncio
async def test_live_monitor_accepts_fresh_public_market_data() -> None:
    monitor = LiveCryptoMonitor()

    accepted = await monitor.ingest_tick(_tick())
    snapshot = monitor.snapshot("BTC")

    assert accepted is True
    assert snapshot is not None
    assert snapshot["source"] == "PUBLIC_READ_ONLY"
    assert snapshot["venue"] == "coinbase-public"
    assert snapshot["symbol"] == "BTC"
    assert snapshot["bid"] < snapshot["ask"]
    assert snapshot["financial_connectivity"] is False
    assert snapshot["real_money_execution"] is False


@pytest.mark.asyncio
async def test_live_monitor_rejects_stale_public_market_data() -> None:
    monitor = LiveCryptoMonitor()
    stale_tick = _tick(observed_at=datetime.now(UTC) - timedelta(minutes=1))

    accepted = await monitor.ingest_tick(stale_tick)

    assert accepted is False
    assert monitor.snapshot("BTC") is None


def test_live_unknown_symbol_has_no_fabricated_snapshot() -> None:
    response = client.get("/live/market-data/DOGE")

    assert response.status_code == 404
    assert response.json()["detail"] == "no live snapshot available for symbol"
