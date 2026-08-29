from services.market_data.live import CoinbasePublicMarketDataAdapter


def test_public_feed_health_starts_disconnected_without_credentials() -> None:
    adapter = CoinbasePublicMarketDataAdapter()

    health = adapter.health()

    assert health.connected is False
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


def test_public_feed_rejects_unsupported_products() -> None:
    try:
        CoinbasePublicMarketDataAdapter(products=("DOGE-USD",))
    except ValueError as error:
        assert "unsupported public products" in str(error)
    else:
        raise AssertionError("unsupported product must fail closed")


def test_public_feed_rejects_invalid_reconnect_intervals() -> None:
    try:
        CoinbasePublicMarketDataAdapter(
            reconnect_min_seconds=5.0,
            reconnect_max_seconds=1.0,
        )
    except ValueError as error:
        assert str(error) == "invalid reconnect interval"
    else:
        raise AssertionError("invalid reconnect interval must fail closed")
