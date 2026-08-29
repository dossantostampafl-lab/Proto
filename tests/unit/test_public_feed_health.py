import pytest

from services.market_data.live import CoinbasePublicMarketDataAdapter


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
    assert health.connected_since is None
    assert health.last_message_at is None
    assert health.last_tick_at is None
    assert health.last_error is None
    assert adapter.products == ("BTC-USD", "ETH-USD", "SOL-USD")
    assert adapter.endpoint == "wss://advanced-trade-ws.coinbase.com"


def test_public_feed_rejects_unsupported_products() -> None:
    with pytest.raises(ValueError, match="unsupported public products"):
        CoinbasePublicMarketDataAdapter(products=("DOGE-USD",))


def test_public_feed_rejects_invalid_reconnect_intervals() -> None:
    with pytest.raises(ValueError, match="invalid reconnect interval"):
        CoinbasePublicMarketDataAdapter(
            reconnect_min_seconds=5.0,
            reconnect_max_seconds=1.0,
        )


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
