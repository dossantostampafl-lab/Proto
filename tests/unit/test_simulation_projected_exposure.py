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


def _request(*, side: Side, quantity: float, current_position_quantity: float) -> SimulationRequest:
    return SimulationRequest(
        order=SimulationOrder(
            market_id="btc-usd-paper",
            asset=Asset.BTC,
            side=side,
            quantity=quantity,
            limit_price=60_100.0 if side == Side.BUY else 59_900.0,
        ),
        snapshot=MarketSnapshot(
            symbol="BTC",
            market_id="btc-usd-paper",
            bid=60_000.0,
            ask=60_010.0,
            observed_at=datetime.now(UTC),
        ),
        current_position_quantity=current_position_quantity,
        current_position_notional=abs(current_position_quantity) * 60_005.0,
        limits=RiskLimits(
            max_order_notional=100_000.0,
            max_position_notional=65_000.0,
            max_slippage_bps=75.0,
        ),
    )


def test_risk_allows_order_that_reduces_existing_long_exposure() -> None:
    result = PaperSimulator().simulate(
        _request(side=Side.SELL, quantity=0.5, current_position_quantity=1.0)
    )

    assert result.accepted is True


def test_risk_rejects_cross_zero_order_when_new_exposure_exceeds_limit() -> None:
    result = PaperSimulator().simulate(
        _request(side=Side.SELL, quantity=2.5, current_position_quantity=1.0)
    )

    assert result.accepted is False
    assert result.reason == "max order notional exceeded"


def test_risk_rejects_projected_position_above_position_limit() -> None:
    request = _request(side=Side.BUY, quantity=0.2, current_position_quantity=1.0)
    request = request.model_copy(
        update={
            "limits": RiskLimits(
                max_order_notional=100_000.0,
                max_position_notional=65_000.0,
                max_slippage_bps=75.0,
            )
        }
    )

    result = PaperSimulator().simulate(request)

    assert result.accepted is False
    assert result.reason == "max position notional exceeded"
