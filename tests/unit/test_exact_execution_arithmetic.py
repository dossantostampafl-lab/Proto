from datetime import UTC, datetime

import pytest

from apps.api.app.models import Asset, MarketSnapshot, Side, SimulationOrder, SimulationRequest
from apps.api.app.simulation import PaperSimulator, SimulationConfig


def _request(*, side: Side, price: float, quantity: float = 0.1) -> SimulationRequest:
    snapshot = MarketSnapshot(
        symbol="BTC",
        market_id="btc-exact-execution",
        bid=price,
        ask=price,
        bid_size=100.0,
        ask_size=100.0,
        observed_at=datetime.now(UTC),
    )
    order = SimulationOrder(
        market_id=snapshot.market_id,
        asset=Asset.BTC,
        side=side,
        quantity=quantity,
        limit_price=price + 1.0 if side == Side.BUY else max(price - 1.0, 0.0001),
    )
    return SimulationRequest(order=order, snapshot=snapshot)


def _simulator() -> PaperSimulator:
    return PaperSimulator(
        SimulationConfig(
            fee_bps=2.5,
            base_slippage_bps=0.0,
            latency_ms=0.0,
            latency_slippage_bps_per_100ms=0.0,
            tick_size=0.01,
            depth_impact_bps_at_full_book=0.0,
        )
    )


def test_buy_price_rounds_up_on_exact_decimal_tick_grid() -> None:
    result = _simulator().simulate(_request(side=Side.BUY, price=100.001))

    assert result.accepted is True
    assert result.fill is not None
    assert result.fill.fill_price == pytest.approx(100.01)
    assert result.fill.fee == pytest.approx(0.00250025)


def test_sell_price_rounds_down_on_exact_decimal_tick_grid() -> None:
    result = _simulator().simulate(_request(side=Side.SELL, price=100.009))

    assert result.accepted is True
    assert result.fill is not None
    assert result.fill.fill_price == pytest.approx(100.0)
    assert result.fill.fee == pytest.approx(0.0025)


def test_decimal_grid_does_not_accumulate_binary_tick_dust() -> None:
    simulator = PaperSimulator(
        SimulationConfig(
            fee_bps=0.0,
            base_slippage_bps=0.0,
            latency_ms=0.0,
            latency_slippage_bps_per_100ms=0.0,
            tick_size=0.1,
            depth_impact_bps_at_full_book=0.0,
        )
    )

    result = simulator.simulate(_request(side=Side.BUY, price=0.3, quantity=1.0))

    assert result.accepted is True
    assert result.fill is not None
    assert result.fill.fill_price == 0.3
    assert result.fill.fee == 0.0
