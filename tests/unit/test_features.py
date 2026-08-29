from datetime import UTC, datetime, timedelta

import pytest

from services.features.core import FeatureWindow, build_feature_frame
from services.market_data.core import MarketTick


def _tick(*, second: int, bid: float, ask: float, volume: float, sequence: int) -> MarketTick:
    return MarketTick(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=second),
        venue="synthetic",
        symbol="BTC",
        bid=bid,
        ask=ask,
        last=(bid + ask) / 2,
        volume=volume,
        bid_size=2.0,
        ask_size=1.0,
        sequence=sequence,
    )


def test_feature_frame_uses_requested_window() -> None:
    ticks = [
        _tick(second=0, bid=99, ask=101, volume=10, sequence=1),
        _tick(second=4, bid=100, ask=102, volume=15, sequence=2),
        _tick(second=8, bid=103, ask=105, volume=25, sequence=3),
    ]

    frame = build_feature_frame(ticks, window=FeatureWindow.S5)

    assert frame.window == FeatureWindow.S5
    assert frame.sample_count == 2
    assert frame.return_simple > 0
    assert frame.log_return > 0
    assert frame.momentum > 0
    assert frame.volume_acceleration > 0
    assert frame.price_velocity > 0
    assert frame.orderbook_imbalance > 0
    assert frame.liquidity_score > 0


def test_feature_frame_is_order_independent() -> None:
    ticks = [
        _tick(second=1, bid=100, ask=101, volume=11, sequence=2),
        _tick(second=0, bid=99, ask=100, volume=10, sequence=1),
        _tick(second=2, bid=101, ask=102, volume=13, sequence=3),
    ]

    forward = build_feature_frame(ticks, window=FeatureWindow.S5)
    reverse = build_feature_frame(reversed(ticks), window=FeatureWindow.S5)

    assert forward == reverse


def test_feature_frame_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="requires at least one market tick"):
        build_feature_frame([], window=FeatureWindow.S1)
