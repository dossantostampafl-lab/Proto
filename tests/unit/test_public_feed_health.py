import asyncio
import json

import pytest

from services.market_data.live import (
    _MAX_PUBLIC_FRAME_BYTES,
    CoinbasePublicMarketDataAdapter,
    PublicCryptoFeedError,
    PublicFeedTimeoutError,
    _receive_with_timeout,
    parse_public_ticker_message,
)


class SilentWebSocket:
    async def recv(self) -> str:
        await asyncio.sleep(0.05)
        return "{}"


def _valid_ticker_message() -> str:
    return json.dumps(
        {
            "channel": "ticker",
            "timestamp": "2026-08-29T20:15:00Z",
            "sequence_num": 42,
            "events": [
                {
                    "tickers": [
                        {
                            "product_id": "BTC-USD",
                            "price": "61000.25",
                            "best_bid": "61000.00",
                            "best_ask": "61000.50",
                            "best_bid_quantity": "1.2",
                            "best_ask_quantity": "0.8",
                            "volume_24_h": "123.4",
                        }
                    ]
                }
            ],
        }
    )


def test_public_feed_health_starts_disconnected_without_credentials() -> None:
    adapter = CoinbasePublicMarketDataAdapter()

    health = adapter.health()

    assert health.connected is False
    assert health.connection_generation == 0
    assert health.connection_attempts == 0
    assert health.reconnect_count == 0
    assert health.frames_received == 0
    assert health.ticks_emitted == 0
    assert health.parse_error_count == 0
    assert health.consecutive_parse_errors == 0
    assert health.message_timeout_count == 0
    assert health.connected_since is None
    assert health.last_message_at is None
    assert health.last_tick_at is None
    assert health.last_error is None
    assert adapter.products == ("BTC-USD", "ETH-USD", "SOL-USD")
    assert adapter.endpoint == "wss://advanced-trade-ws.coinbase.com"
    assert adapter.message_timeout_seconds == 35.0
    assert adapter.max_consecutive_parse_errors == 3


def test_public_feed_rejects_unsupported_products() -> None:
    with pytest.raises(ValueError, match="unsupported public products"):
        CoinbasePublicMarketDataAdapter(products=("DOGE-USD",))


def test_public_feed_rejects_invalid_reconnect_intervals() -> None:
    with pytest.raises(ValueError, match="invalid reconnect interval"):
        CoinbasePublicMarketDataAdapter(
            reconnect_min_seconds=5.0,
            reconnect_max_seconds=1.0,
        )


@pytest.mark.parametrize("value", [0.0, -1.0, float("inf"), float("nan")])
def test_public_feed_rejects_invalid_message_timeout(value: float) -> None:
    with pytest.raises(ValueError, match="message_timeout_seconds"):
        CoinbasePublicMarketDataAdapter(message_timeout_seconds=value)


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_public_feed_rejects_invalid_parse_error_budget(value: object) -> None:
    with pytest.raises(ValueError, match="max_consecutive_parse_errors"):
        CoinbasePublicMarketDataAdapter(max_consecutive_parse_errors=value)  # type: ignore[arg-type]


def test_public_feed_rejects_oversized_wire_payload_before_json_decode() -> None:
    oversized = "x" * (_MAX_PUBLIC_FRAME_BYTES + 1)

    with pytest.raises(PublicCryptoFeedError, match="exceeds size limit"):
        parse_public_ticker_message(oversized)


def test_public_feed_rejects_excessive_ticker_event_cardinality() -> None:
    payload = {
        "channel": "ticker",
        "timestamp": "2026-08-29T20:15:00Z",
        "sequence_num": 42,
        "events": [{"tickers": []} for _ in range(33)],
    }

    with pytest.raises(PublicCryptoFeedError, match="event count exceeds limit"):
        parse_public_ticker_message(payload)


def test_public_feed_parse_degradation_survives_heartbeat_until_ticker_recovers() -> None:
    adapter = CoinbasePublicMarketDataAdapter(max_consecutive_parse_errors=2)

    assert adapter._parse_message("not-json") == []
    degraded = adapter.health()
    assert degraded.parse_error_count == 1
    assert degraded.consecutive_parse_errors == 1
    assert degraded.last_error == "PublicCryptoFeedError"

    assert adapter._parse_message('{"channel":"heartbeats"}') == []
    heartbeat_only = adapter.health()
    assert heartbeat_only.parse_error_count == 1
    assert heartbeat_only.consecutive_parse_errors == 1
    assert heartbeat_only.last_error == "PublicCryptoFeedError"

    recovered_ticks = adapter._parse_message(_valid_ticker_message())
    assert len(recovered_ticks) == 1
    recovered = adapter.health()
    assert recovered.parse_error_count == 1
    assert recovered.consecutive_parse_errors == 0
    assert recovered.last_error is None


def test_public_feed_escalates_after_parse_error_budget_is_exhausted() -> None:
    adapter = CoinbasePublicMarketDataAdapter(max_consecutive_parse_errors=2)

    assert adapter._parse_message("not-json") == []
    assert adapter._parse_message('{"channel":"heartbeats"}') == []
    with pytest.raises(PublicCryptoFeedError, match="invalid public feed payload"):
        adapter._parse_message("still-not-json")

    health = adapter.health()
    assert health.parse_error_count == 2
    assert health.consecutive_parse_errors == 2
    assert health.last_error == "PublicCryptoFeedError"


@pytest.mark.asyncio
async def test_public_feed_timeout_raises_fail_closed_error() -> None:
    with pytest.raises(PublicFeedTimeoutError, match="public feed message timeout"):
        await _receive_with_timeout(SilentWebSocket(), 0.001)


@pytest.mark.parametrize(
    "endpoint,match",
    [
        ("ws://advanced-trade-ws.coinbase.com", "must use wss"),
        ("wss://example.invalid", "host is not allowlisted"),
        (
            "wss://user:secret@advanced-trade-ws.coinbase.com",
            "must not contain credentials",
        ),
        (
            "wss://advanced-trade-ws.coinbase.com:8443",
            "standard TLS port",
        ),
        (
            "wss://advanced-trade-ws.coinbase.com/feed?token=ignored",
            "must not contain path parameters or query data",
        ),
    ],
)
def test_public_feed_rejects_noncanonical_websocket_endpoints(
    endpoint: str,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        CoinbasePublicMarketDataAdapter(endpoint=endpoint)
