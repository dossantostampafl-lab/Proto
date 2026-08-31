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
    side: Side = Side.BUY,
    quantity: float = 0.1,
    volatility: float = 0.2,
    bid_size: float = 1.0,
    ask_size: float = 1.0,
    current_quantity: float = 0.0,
) -> SimulationRequest:
    return SimulationRequest(
        order=SimulationOrder(
            market_id="btc-paper",
            asset=Asset.BTC,
            side=side,
            quantity=quantity,
            limit_price=60_100.0 if side == Side.BUY else 59_900.0,
        ),
        snapshot=MarketSnapshot(
            symbol="BTC",
            market_id="btc-paper",
            bid=60_000.0,
            ask=60_010.0,
            bid_size=bid_size,
            ask_size=ask_size,
            volatility=volatility,
        ),
        current_position_quantity=current_quantity,
        limits=RiskLimits(
            max_order_notional=50_000.0,
            max_position_notional=50_000.0,
            max_slippage_bps=100.0,
            max_gross_exposure=100_000.0,
            max_asset_concentration=1.0,
            max_drawdown=5_000.0,
            max_volatility=1.0,
            max_order_to_book_ratio=0.50,
        ),
    )


def test_rejects_new_risk_when_volatility_exceeds_limit() -> None:
    accepted, reason = RiskEngine().validate(
        _request(volatility=1.2),
        estimated_slippage_bps=5.0,
    )

    assert accepted is False
    assert reason == "max volatility exceeded"


def test_allows_strict_reduction_during_high_volatility() -> None:
    accepted, reason = RiskEngine().validate(
        _request(
            side=Side.SELL,
            quantity=0.1,
            volatility=1.2,
            current_quantity=0.2,
        ),
        estimated_slippage_bps=5.0,
    )

    assert accepted is True
    assert reason == "accepted"


def test_rejects_order_that_consumes_too_much_top_book() -> None:
    accepted, reason = RiskEngine().validate(
        _request(quantity=0.2, ask_size=0.2),
        estimated_slippage_bps=5.0,
    )

    assert accepted is False
    assert reason == "insufficient top-of-book liquidity"


def test_uses_side_specific_book_liquidity() -> None:
    accepted, reason = RiskEngine().validate(
        _request(
            side=Side.SELL,
            quantity=0.1,
            bid_size=0.5,
            ask_size=0.01,
        ),
        estimated_slippage_bps=5.0,
    )

    assert accepted is True
    assert reason == "accepted"
