from datetime import UTC, datetime, timedelta

import pytest

from services.features.microstructure import (
    build_microstructure_snapshot,
    calculate_order_flow_imbalance,
)
from services.market_data.core import MarketTick


def _tick(
    *,
    sequence: int,
    bid: float,
    ask: float,
    bid_size: float,
    ask_size: float,
) -> MarketTick:
    return MarketTick(
        timestamp=datetime(2026, 8, 31, 14, 30, tzinfo=UTC)
        + timedelta(milliseconds=sequence),
        venue="TEST",
        symbol="BTC",
        bid=bid,
        ask=ask,
        last=(bid + ask) / 2.0,
        volume=100.0 + sequence,
        bid_size=bid_size,
        ask_size=ask_size,
        sequence=sequence,
    )


def test_ofi_is_positive_when_bid_strengthens_and_ask_weakens() -> None:
    previous = _tick(sequence=1, bid=100.0, ask=101.0, bid_size=10.0, ask_size=10.0)
    current = _tick(sequence=2, bid=100.0, ask=101.0, bid_size=15.0, ask_size=6.0)

    assert calculate_order_flow_imbalance(previous, current) == 9.0


def test_microstructure_snapshot_exposes_depth_spread_and_microprice() -> None:
    previous = _tick(sequence=1, bid=100.0, ask=101.0, bid_size=10.0, ask_size=10.0)
    current = _tick(sequence=2, bid=100.5, ask=101.0, bid_size=20.0, ask_size=5.0)

    snapshot = build_microstructure_snapshot(previous, current)

    assert snapshot.order_flow_imbalance > 0.0
    assert snapshot.normalized_ofi > 0.0
    assert snapshot.total_depth == 25.0
    assert snapshot.spread_bps > 0.0
    assert snapshot.microprice_deviation > 0.0
    assert snapshot.liquidity_score > 0.0


def test_ofi_rejects_non_monotonic_sequence() -> None:
    previous = _tick(sequence=2, bid=100.0, ask=101.0, bid_size=10.0, ask_size=10.0)
    current = _tick(sequence=2, bid=100.0, ask=101.0, bid_size=11.0, ask_size=9.0)

    with pytest.raises(ValueError, match="strictly increasing sequence"):
        calculate_order_flow_imbalance(previous, current)
