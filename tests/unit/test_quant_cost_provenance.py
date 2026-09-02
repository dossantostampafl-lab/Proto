from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from services.quant.pipeline import QuantPipelineInput


def _base_payload() -> dict[str, object]:
    return {
        "market_id": "btc-cost-provenance",
        "symbol": "BTC",
        "observed_at": datetime(2026, 9, 2, 12, 0, tzinfo=UTC),
        "market_probability": 0.52,
        "volatility": 0.2,
        "imbalance": 0.1,
        "liquidity_score": 0.9,
        "fees": 0.001,
        "slippage": 0.001,
        "spread_cost": 0.0005,
        "hedge_cost": 0.0,
        "latency_penalty": 0.0002,
    }


@pytest.mark.parametrize(
    "field",
    (
        "liquidity_score",
        "fees",
        "slippage",
        "spread_cost",
        "hedge_cost",
        "latency_penalty",
    ),
)
def test_quant_pipeline_rejects_missing_cost_or_liquidity_provenance(field: str) -> None:
    payload = _base_payload()
    payload.pop(field)

    with pytest.raises(ValidationError):
        QuantPipelineInput(**payload)


def test_quant_pipeline_accepts_explicit_zero_costs() -> None:
    payload = _base_payload()
    payload.update(
        fees=0.0,
        slippage=0.0,
        spread_cost=0.0,
        hedge_cost=0.0,
        latency_penalty=0.0,
    )

    request = QuantPipelineInput(**payload)

    assert request.fees == 0.0
    assert request.slippage == 0.0
    assert request.spread_cost == 0.0
    assert request.hedge_cost == 0.0
    assert request.latency_penalty == 0.0
