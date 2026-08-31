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
    quantity: float = 1.0,
    current_quantity: float = 0.0,
    gross: float = 0.0,
    asset_exposure: float = 0.0,
    max_gross: float = 75_000.0,
    max_concentration: float = 0.80,
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
        current_gross_exposure=gross,
        current_asset_exposure=asset_exposure,
        limits=RiskLimits(
            max_order_notional=50_000.0,
            max_position_notional=50_000.0,
            max_slippage_bps=100.0,
            max_gross_exposure=max_gross,
            max_asset_concentration=max_concentration,
        ),
    )


def test_rejects_projected_gross_exposure_breach() -> None:
    request = _request(gross=70_000.0, asset_exposure=20_000.0)

    accepted, reason = RiskEngine().validate(request, estimated_slippage_bps=5.0)

    assert accepted is False
    assert reason == "max gross exposure exceeded"


def test_rejects_projected_asset_concentration_breach() -> None:
    request = _request(
        gross=50_000.0,
        asset_exposure=10_000.0,
        quantity=2.0,
        max_concentration=0.30,
    )

    accepted, reason = RiskEngine().validate(request, estimated_slippage_bps=5.0)

    assert accepted is False
    assert reason == "max asset concentration exceeded"


def test_risk_reducing_order_replaces_current_asset_exposure_in_gross_projection() -> None:
    request = _request(
        side=Side.SELL,
        quantity=1.0,
        current_quantity=2.0,
        gross=60_000.0,
        asset_exposure=20_000.0,
        max_concentration=0.80,
    )

    accepted, reason = RiskEngine().validate(request, estimated_slippage_bps=5.0)

    assert accepted is True
    assert reason == "accepted"
