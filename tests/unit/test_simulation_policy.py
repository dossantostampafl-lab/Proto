from apps.api.app.models import (
    Asset,
    MarketSnapshot,
    RiskLimits,
    Side,
    SimulationOrder,
    SimulationRequest,
)
from apps.api.app.settings import settings
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
        current_gross_exposure=1.0,
        current_asset_exposure=1.0,
        current_drawdown=0.0,
        limits=RiskLimits(
            max_order_notional=1_000_000.0,
            max_position_notional=1_000_000.0,
            max_slippage_bps=1_000.0,
            max_gross_exposure=1_000_000.0,
            max_asset_concentration=1.0,
            max_drawdown=1_000_000.0,
            max_volatility=10.0,
            max_order_to_book_ratio=1.0,
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
    assert effective.limits.max_gross_exposure == settings.simulation_max_gross_exposure
    assert (
        effective.limits.max_asset_concentration
        == settings.simulation_max_asset_concentration
    )
    assert effective.limits.max_drawdown == settings.max_daily_drawdown
    assert effective.limits.max_volatility == settings.simulation_max_volatility
    assert (
        effective.limits.max_order_to_book_ratio
        == settings.simulation_max_order_to_book_ratio
    )


def test_authoritative_policy_uses_canonical_position_when_client_underreports() -> None:
    effective = authoritative_simulation_request(
        _request(),
        {
            "positions": [{"asset": "BTC", "quantity": 0.3}],
            "gross_exposure": 42_000.0,
            "exposure_by_asset": {"BTC": 18_000.0, "ETH": 24_000.0},
            "realized_drawdown": 6_000.0,
        },
        max_order_notional=10_000.0,
        max_position_notional=25_000.0,
        max_slippage_bps=75.0,
    )

    expected_notional = 0.3 * ((60_000.0 + 60_010.0) / 2.0)
    assert effective.current_position_notional == expected_notional
    assert effective.current_gross_exposure == 42_000.0
    assert effective.current_asset_exposure == 18_000.0
    assert effective.current_drawdown == 6_000.0


def test_authoritative_policy_ignores_client_drawdown_spoofing() -> None:
    request = _request().model_copy(update={"current_drawdown": 0.0})
    effective = authoritative_simulation_request(
        request,
        {"positions": [], "realized_drawdown": 7_500.0},
        max_order_notional=10_000.0,
        max_position_notional=25_000.0,
        max_slippage_bps=75.0,
    )

    assert effective.current_drawdown == 7_500.0


def test_authoritative_policy_does_not_treat_loss_from_zero_as_peak_drawdown() -> None:
    effective = authoritative_simulation_request(
        _request(),
        {
            "positions": [],
            "total_pnl_after_fees": -7_500.0,
            "realized_drawdown": 2_000.0,
        },
        max_order_notional=10_000.0,
        max_position_notional=25_000.0,
        max_slippage_bps=75.0,
    )

    assert effective.current_drawdown == 2_000.0


def test_authoritative_policy_preserves_stricter_client_limits() -> None:
    request = _request().model_copy(
        update={
            "limits": RiskLimits(
                max_order_notional=5_000.0,
                max_position_notional=12_000.0,
                max_slippage_bps=20.0,
                max_gross_exposure=50_000.0,
                max_asset_concentration=0.60,
                max_drawdown=2_500.0,
                max_volatility=0.8,
                max_order_to_book_ratio=0.25,
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
