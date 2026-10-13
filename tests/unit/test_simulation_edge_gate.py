from apps.api.app.models import (
    Asset,
    MarketSnapshot,
    RiskLimits,
    Side,
    SimulationOrder,
    SimulationRequest,
)
from apps.api.app.settings import settings
from apps.api.app.simulation import RiskEngine
from apps.api.app.simulation_policy import authoritative_simulation_request


def _request(
    *,
    side: Side = Side.BUY,
    imbalance: float = 0.0,
    volatility: float = 0.2,
    current_quantity: float = 0.0,
    min_net_edge: float | None = 0.01,
    min_confidence: float | None = None,
) -> SimulationRequest:
    return SimulationRequest(
        order=SimulationOrder(
            market_id="btc-paper", asset=Asset.BTC, side=side, quantity=1.0, limit_price=100.0
        ),
        snapshot=MarketSnapshot(
            symbol="BTC",
            market_id="btc-paper",
            bid=99.99,
            ask=100.0,
            ask_size=1e6,
            bid_size=1e6,
            market_probability=0.5,
            imbalance=imbalance,
            volatility=volatility,
        ),
        current_position_quantity=current_quantity,
        current_open_positions=1 if current_quantity else 0,
        limits=RiskLimits(
            max_order_notional=1e9,
            max_position_notional=1e9,
            max_gross_exposure=1e9,
            max_slippage_bps=1e4,
            min_net_edge=min_net_edge,
            min_confidence=min_confidence,
        ),
    )


def _validate(request: SimulationRequest) -> tuple[bool, str]:
    return RiskEngine().validate(request, estimated_slippage_bps=4.0, fee_bps=2.0)


def test_buy_with_model_edge_above_costs_passes() -> None:
    # Strong bid imbalance lifts the model probability ~8.7 points over the market's 50%.
    assert _validate(_request(imbalance=1.0)) == (True, "accepted")


def test_buy_without_model_edge_is_rejected() -> None:
    assert _validate(_request(imbalance=0.0)) == (False, "min net edge not met")


def test_edge_is_directional_selling_into_a_bullish_model_is_rejected() -> None:
    assert _validate(_request(side=Side.SELL, imbalance=1.0)) == (False, "min net edge not met")


def test_low_model_confidence_is_rejected() -> None:
    request = _request(imbalance=1.0, volatility=1.2, min_confidence=0.9)
    assert _validate(request) == (False, "min confidence not met")


def test_risk_reducing_order_is_exempt_from_research_gates() -> None:
    request = _request(side=Side.SELL, imbalance=1.0, current_quantity=2.0)
    assert _validate(request) == (True, "accepted")


def test_gates_are_off_by_default_and_server_floor_applies_when_enabled(monkeypatch) -> None:
    loose = _request(min_net_edge=None, min_confidence=None)
    snapshot = {"positions": [], "gross_exposure": 0.0, "open_position_count": 0}
    caps = {"max_order_notional": 1e9, "max_position_notional": 1e9, "max_slippage_bps": 1e4}

    monkeypatch.setattr(settings, "simulation_edge_gate_enabled", False)
    off = authoritative_simulation_request(loose, snapshot, **caps)
    assert off.limits.min_net_edge is None
    assert off.limits.min_confidence is None

    monkeypatch.setattr(settings, "simulation_edge_gate_enabled", True)
    monkeypatch.setattr(settings, "minimum_net_edge", 0.02)
    monkeypatch.setattr(settings, "minimum_confidence", 0.6)
    on = authoritative_simulation_request(_request(min_net_edge=0.001), snapshot, **caps)
    assert on.limits.min_net_edge == 0.02  # a client cannot loosen the server floor
    assert on.limits.min_confidence == 0.6

    strict = authoritative_simulation_request(_request(min_net_edge=0.05), snapshot, **caps)
    assert strict.limits.min_net_edge == 0.05  # a client may be stricter
