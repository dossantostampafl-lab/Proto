from datetime import UTC, datetime, timedelta

from apps.api.app.live_payloads import (
    age_seconds,
    market_payload,
    orderbook_payload,
    source_to_server_delta_ms,
)
from services.market_data import MarketTick, compute_orderbook_metrics


def _tick() -> MarketTick:
    return MarketTick(
        timestamp=datetime(2026, 8, 30, 15, 0, tzinfo=UTC),
        venue="coinbase-public",
        symbol="BTC",
        bid=60_000.0,
        ask=60_002.0,
        last=60_001.0,
        volume=120.0,
        bid_size=2.0,
        ask_size=1.0,
        sequence=7,
    )


def test_payload_time_helpers_are_deterministic() -> None:
    source_at = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)
    received_at = source_at + timedelta(milliseconds=125)

    assert age_seconds(source_at, now=received_at) == 0.125
    assert source_to_server_delta_ms(
        source_at=source_at,
        received_at=received_at,
    ) == 125.0


def test_market_payload_preserves_read_only_contract() -> None:
    tick = _tick()
    received_at = tick.timestamp + timedelta(milliseconds=20)

    payload = market_payload(
        tick,
        received_at=received_at,
        connection_generation=3,
    )

    assert payload["source"] == "PUBLIC_READ_ONLY"
    assert payload["symbol"] == "BTC"
    assert payload["connection_generation"] == 3
    assert payload["source_to_server_delta_ms"] == 20.0
    assert payload["financial_connectivity"] is False
    assert payload["real_money_execution"] is False


def test_orderbook_payload_preserves_read_only_contract() -> None:
    tick = _tick()
    received_at = tick.timestamp + timedelta(milliseconds=30)
    book = compute_orderbook_metrics(tick)

    payload = orderbook_payload(
        tick,
        book,
        received_at=received_at,
        connection_generation=4,
    )

    assert payload["source"] == "PUBLIC_READ_ONLY"
    assert payload["best_bid"] == tick.bid
    assert payload["best_ask"] == tick.ask
    assert payload["connection_generation"] == 4
    assert payload["financial_connectivity"] is False
    assert payload["real_money_execution"] is False
