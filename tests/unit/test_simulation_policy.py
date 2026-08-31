from apps.api.app.models import (
    Asset,
    MarketSnapshot,
    RiskLimits,
    Side,
    SimulationOrder,
    SimulationRequest,
)
from apps.api.app.simulation_policy import authoritative_simulation_request


def _request() -> SimulationRequest:
    return SimulationRequest(
        order=SimulationOrder(
            market_id="btc-usd-paper",
            asset=Asset.BTC,
            side=Side.BUY,
            quantity=0.1,
            limit_price=61_000.0,
        ),
        snapshot=MarketSnapshot(
            symbol="BTC",
            market_id="btc-usd-paper",
            bid=60_000.0,
            ask=60_010.0,
        ),
        current_position_notional=1.0,
        limits=RiskLimits(
            max_order_notional=1_000_000.0,
            max_position_notional=1_000_000.0,
            max_slippage_bps=1_000.0,
        ),
    )


def test_authoritative_policy_caps_client_risk_limits() -> None:
    effective = authoritative_simulation_request(
        _request(),
        {"positions": []},
        max_order_notional=10_000.0,
        max_position_notional=25_000.0,
        max_slippage_bps=75.0,
    )

    assert effective.limits.max_order_notional == 10_000.0
    assert effective.limits.max_position_notional == 25_000.0
    assert effective.limits.max_slippage_bps == 75.0


def test_authoritative_policy_uses_canonical_position_when_client_underreports() -> None:
    effective = authoritative_simulation_request(
        _request(),
        {"positions": [{"asset": "BTC", "quantity": 0.3}]},
        max_order_notional=10_000.0,
        max_position_notional=25_000.0,
        max_slippage_bps=75.0,
    )

    expected_notional = 0.3 * ((60_000.0 + 60_010.0) / 2.0)
    assert effective.current_position_notional == expected_notional


def test_authoritative_policy_preserves_stricter_client_limits() -> None:
    request = _request().model_copy(
        update={
            "limits": RiskLimits(
                max_order_notional=5_000.0,
                max_position_notional=12_000.0,
                max_slippage_bps=20.0,
            )
        }
    )
    effective = authoritative_simulation_request(
        request,
        {"positions": []},
        max_order_notional=10_000.0,
        max_position_notional=25_000.0,
        max_slippage_bps=75.0,
    )

    assert effective.limits == request.limits
