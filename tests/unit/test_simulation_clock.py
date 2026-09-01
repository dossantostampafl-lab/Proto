from datetime import UTC, datetime

from apps.api.app.models import (
    Asset,
    MarketSnapshot,
    RiskLimits,
    Side,
    SimulationOrder,
    SimulationRequest,
)
from apps.api.app.simulation import PaperSimulator


def test_simulated_fill_uses_reference_clock_instead_of_wall_clock() -> None:
    replay_clock = datetime(2025, 5, 1, 12, 30, tzinfo=UTC)
    order = SimulationOrder(
        market_id="btc-replay",
        asset=Asset.BTC,
        side=Side.BUY,
        quantity=0.01,
        limit_price=61_000.0,
        created_at=replay_clock,
    )
    request = SimulationRequest(
        order=order,
        snapshot=MarketSnapshot(
            symbol="BTC",
            market_id=order.market_id,
            bid=60_000.0,
            ask=60_010.0,
            bid_size=10.0,
            ask_size=10.0,
            volatility=0.2,
            observed_at=replay_clock,
        ),
        limits=RiskLimits(
            max_order_notional=100_000.0,
            max_position_notional=100_000.0,
            max_slippage_bps=1_000.0,
            max_gross_exposure=200_000.0,
            max_asset_concentration=1.0,
            max_drawdown=10_000.0,
            max_volatility=1.0,
            max_order_to_book_ratio=1.0,
        ),
    )
    simulator = PaperSimulator(reference_time_provider=lambda: replay_clock)

    result = simulator.simulate(request)

    assert result.accepted is True
    assert result.fill is not None
    assert result.fill.filled_at == replay_clock
