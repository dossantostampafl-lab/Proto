from apps.api.app.models import (
    Asset,
    MarketSnapshot,
    RiskLimits,
    Side,
    SimulationOrder,
    SimulationRequest,
)
from apps.api.app.simulation import RiskEngine


def _request(
    *,
    side: Side,
    quantity: float,
    current_quantity: float,
    drawdown: float,
    max_drawdown: float = 5_000.0,
) -> SimulationRequest:
    return SimulationRequest(
        order=SimulationOrder(
            market_id="btc-paper",
            asset=Asset.BTC,
            side=side,
            quantity=quantity,
            limit_price=10_000.0,
        ),
        snapshot=MarketSnapshot(
            symbol="BTC",
            market_id="btc-paper",
            bid=9_990.0,
            ask=10_000.0,
        ),
        current_position_quantity=current_quantity,
        current_gross_exposure=abs(current_quantity) * 10_000.0,
        current_asset_exposure=abs(current_quantity) * 10_000.0,
        current_drawdown=drawdown,
        limits=RiskLimits(
            max_order_notional=50_000.0,
            max_position_notional=50_000.0,
            max_slippage_bps=100.0,
            max_gross_exposure=75_000.0,
            max_asset_concentration=1.0,
            max_drawdown=max_drawdown,
        ),
    )


def test_drawdown_ceiling_blocks_new_risk() -> None:
    request = _request(
        side=Side.BUY,
        quantity=1.0,
        current_quantity=1.0,
        drawdown=5_000.0,
    )

    accepted, reason = RiskEngine().validate(request, estimated_slippage_bps=5.0)

    assert accepted is False
    assert reason == "max drawdown exceeded"


def test_drawdown_ceiling_allows_strict_risk_reduction() -> None:
    request = _request(
        side=Side.SELL,
        quantity=1.0,
        current_quantity=2.0,
        drawdown=5_000.0,
    )

    accepted, reason = RiskEngine().validate(request, estimated_slippage_bps=5.0)

    assert accepted is True
    assert reason == "accepted"


def test_cross_zero_order_is_not_drawdown_reducing() -> None:
    request = _request(
        side=Side.SELL,
        quantity=3.0,
        current_quantity=2.0,
        drawdown=5_000.0,
    )

    accepted, reason = RiskEngine().validate(request, estimated_slippage_bps=5.0)

    assert accepted is False
    assert reason == "max drawdown exceeded"
