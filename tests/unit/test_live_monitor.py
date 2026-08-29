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
    assert body["expected_symbols"] == ["BTC", "ETH", "SOL"]
    assert body["feed_health"]["connected"] is False
    assert body["feed_health"]["financial_connectivity"] is False
    assert body["feed_health"]["real_money_execution"] is False
    assert body["financial_connectivity"] is False
    assert body["real_money_execution"] is False


def test_live_source_health_is_read_only_and_account_free() -> None:
    response = client.get("/live/source-health")
    body = response.json()

    assert response.status_code == 200
    assert body["source"] == "PUBLIC_READ_ONLY"
    assert body["connected"] is False
    assert body["connection_attempts"] == 0
    assert body["reconnect_count"] == 0
    assert body["expected_symbols"] == ["BTC", "ETH", "SOL"]
    assert body["financial_connectivity"] is False
    assert body["real_money_execution"] is False


def test_live_readiness_fails_closed_without_fresh_frames() -> None:
    response = client.get("/live/ready")
    body = response.json()

    assert response.status_code == 503
    assert body["status"] == "not_ready"
    assert body["all_symbols_fresh"] is False
    assert body["missing_symbols"] == ["BTC", "ETH", "SOL"]
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
async def test_live_monitor_requires_complete_symbol_coverage_for_status() -> None:
    monitor = LiveCryptoMonitor()

    assert await monitor.ingest_tick(_tick()) is True
    partial = monitor.status()

    assert partial["receiving_data"] is True
    assert partial["complete"] is False
    assert partial["all_symbols_fresh"] is False
    assert partial["fresh_symbols"] == ["BTC"]
    assert partial["missing_symbols"] == ["ETH", "SOL"]
    assert partial["symbol_health"]["BTC"]["fresh"] is True
    assert partial["symbol_health"]["ETH"]["observed"] is False

    now = datetime.now(UTC)
    eth = _tick(observed_at=now).model_copy(
        update={
            "symbol": "ETH",
            "bid": 3_000.0,
            "ask": 3_001.0,
            "last": 3_000.5,
        }
    )
    sol = _tick(observed_at=now).model_copy(
        update={
            "symbol": "SOL",
            "bid": 140.0,
            "ask": 140.1,
            "last": 140.05,
        }
    )

    assert await monitor.ingest_tick(eth) is True
    assert await monitor.ingest_tick(sol) is True
    complete = monitor.status()

    assert complete["complete"] is True
    assert complete["all_symbols_fresh"] is True
    assert complete["missing_symbols"] == []
    assert complete["stale_symbols"] == []
    assert complete["fresh_symbols"] == ["BTC", "ETH", "SOL"]


@pytest.mark.asyncio
async def test_live_monitor_builds_descriptive_analytics_from_bounded_history() -> None:
    monitor = LiveCryptoMonitor()
    started_at = datetime.now(UTC)
    first = _tick(observed_at=started_at, sequence=1)
    second = first.model_copy(
        update={
            "timestamp": started_at + timedelta(milliseconds=100),
            "bid": 60_010.0,
            "ask": 60_011.0,
            "last": 60_010.5,
            "sequence": 2,
        }
    )

    assert await monitor.ingest_tick(first) is True
    assert await monitor.ingest_tick(second) is True
    analytics = monitor.analytics("BTC")

    assert analytics is not None
    assert analytics["source"] == "PUBLIC_READ_ONLY_DESCRIPTIVE"
    assert analytics["sample_count"] == 2
    assert analytics["simple_return"] > 0.0
    assert analytics["realized_volatility"] > 0.0
    assert analytics["financial_connectivity"] is False
    assert analytics["real_money_execution"] is False


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


def test_live_unknown_symbol_has_no_fabricated_analytics() -> None:
    response = client.get("/live/analytics/DOGE")

    assert response.status_code == 404
    assert response.json()["detail"] == "no live analytics available for symbol"
