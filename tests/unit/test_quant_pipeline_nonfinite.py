from datetime import UTC, datetime
import math

import pytest
from pydantic import ValidationError

from services.quant.pipeline import CalibrationSample, QuantPipelineInput


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("market_probability", math.nan),
        ("volatility", math.inf),
        ("imbalance", math.nan),
        ("liquidity_score", math.inf),
        ("fees", math.inf),
        ("slippage", math.nan),
        ("spread_cost", math.inf),
        ("hedge_cost", math.nan),
        ("latency_penalty", math.inf),
        ("minimum_edge", math.nan),
        ("calibration_prior_strength", math.inf),
        ("hawkes_mu", math.inf),
        ("hawkes_alpha", math.nan),
        ("hawkes_beta", math.inf),
    ],
)
def test_quant_pipeline_input_rejects_nonfinite_values(field: str, value: float) -> None:
    payload = {
        "market_id": "btc-research",
        "symbol": "BTC",
        "observed_at": datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
        "market_probability": 0.5,
        "volatility": 0.2,
        "imbalance": 0.0,
        field: value,
    }

    with pytest.raises(ValidationError):
        QuantPipelineInput(**payload)


def test_calibration_sample_rejects_nonfinite_probability() -> None:
    with pytest.raises(ValidationError):
        CalibrationSample(probability=math.nan, outcome=1)
