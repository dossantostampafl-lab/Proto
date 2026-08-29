import math

import pytest
from pydantic import ValidationError

from apps.api.app.models import MarketSnapshot, RiskLimits, SimulationOrder


@pytest.mark.parametrize("field", ["bid", "ask", "volatility", "market_probability"])
@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_market_snapshot_rejects_non_finite_values(field: str, value: float) -> None:
    payload = {
        "symbol": "BTC",
        "market_id": "btc-security-test",
        "bid": 60_000.0,
        "ask": 60_010.0,
        "volatility": 0.2,
        "market_probability": 0.5,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        MarketSnapshot.model_validate(payload)


@pytest.mark.parametrize("field", ["quantity", "limit_price"])
def test_simulation_order_rejects_positive_infinity(field: str) -> None:
    payload = {
        "market_id": "btc-security-test",
        "asset": "BTC",
        "side": "BUY",
        "quantity": 0.1,
        "limit_price": 60_000.0,
    }
    payload[field] = math.inf

    with pytest.raises(ValidationError):
        SimulationOrder.model_validate(payload)


def test_risk_limits_reject_infinite_caps() -> None:
    with pytest.raises(ValidationError):
        RiskLimits(max_order_notional=math.inf)
