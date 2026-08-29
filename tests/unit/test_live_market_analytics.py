from datetime import UTC, datetime, timedelta

import pytest

from services.analytics.live_market import calculate_live_market_analytics
from services.market_data.core import MarketTick


def _tick(*, timestamp: datetime, bid: float, ask: float, sequence: int) -> MarketTick:
    return MarketTick(
        timestamp=timestamp,
        venue="public-read-only",
        symbol="BTC",
        bid=bid,
        ask=ask,
        last=(bid + ask) / 2.0,
        volume=10.0,
        bid_size=2.0,
        ask_size=1.0,
        sequence=sequence,
    )


def test_live_market_analytics_are_descriptive_and_deterministic() -> None:
    start = datetime(2026, 8, 29, tzinfo=UTC)
    ticks = [
        _tick(timestamp=start, bid=99.5, ask=100.5, sequence=1),
        _tick(timestamp=start + timedelta(seconds=1), bid=100.5, ask=101.5, sequence=2),
        _tick(timestamp=start + timedelta(seconds=2), bid=101.5, ask=102.5, sequence=3),
    ]

    result = calculate_live_market_analytics(ticks)

    assert result.symbol == "BTC"
    assert result.sample_count == 3
    assert result.first_mid == pytest.approx(100.0)
    assert result.last_mid == pytest.approx(102.0)
    assert result.simple_return == pytest.approx(0.02)
    assert result.log_return > 0.0
    assert result.realized_volatility > 0.0
    assert result.current_spread_bps > 0.0
    assert -1.0 <= result.current_imbalance <= 1.0
    assert result.observation_span_seconds == 2.0


def test_live_market_analytics_require_one_symbol() -> None:
    start = datetime(2026, 8, 29, tzinfo=UTC)
    btc = _tick(timestamp=start, bid=99.5, ask=100.5, sequence=1)
    eth = btc.model_copy(update={"symbol": "ETH", "sequence": 2})

    with pytest.raises(ValueError):
        calculate_live_market_analytics([btc, eth])
