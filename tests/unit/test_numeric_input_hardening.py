import math

import pytest
from pydantic import ValidationError

from apps.api.app.models import (
    MarketSnapshot,
    MarkPrice,
    RiskLimits,
    SimulationOrder,
    SimulationRequest,
)


@pytest.mark.parametrize(
    "field",
    ["bid", "ask", "bid_size", "ask_size", "volatility", "imbalance", "market_probability"],
)
@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_market_snapshot_rejects_non_finite_values(field: str, value: float) -> None:
    payload = {
        "symbol": "BTC",
        "market_id": "btc-security-test",
        "bid": 60_000.0,
        "ask": 60_010.0,
        "bid_size": 2.0,
        "ask_size": 3.0,
        "volatility": 0.2,
        "imbalance": 0.1,
        "market_probability": 0.5,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        MarketSnapshot.model_validate(payload)


@pytest.mark.parametrize("field", ["quantity", "limit_price"])
@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_simulation_order_rejects_non_finite_values(field: str, value: float) -> None:
    payload = {
        "market_id": "btc-security-test",
        "asset": "BTC",
        "side": "BUY",
        "quantity": 0.1,
        "limit_price": 60_000.0,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        SimulationOrder.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    ["max_order_notional", "max_position_notional", "max_slippage_bps"],
)
def test_risk_limits_reject_non_finite_caps(field: str) -> None:
    with pytest.raises(ValidationError):
        RiskLimits.model_validate({field: math.inf})


def test_simulation_request_rejects_non_finite_client_exposure() -> None:
    order = SimulationOrder(
        market_id="btc-security-test",
        asset="BTC",
        side="BUY",
        quantity=0.1,
        limit_price=60_000.0,
    )
    snapshot = MarketSnapshot(
        symbol="BTC",
        market_id="btc-security-test",
        bid=60_000.0,
        ask=60_010.0,
    )

    with pytest.raises(ValidationError):
        SimulationRequest(
            order=order,
            snapshot=snapshot,
            current_position_quantity=math.nan,
        )


def test_mark_price_rejects_non_finite_price() -> None:
    with pytest.raises(ValidationError):
        MarkPrice(asset="BTC", price=math.inf)
