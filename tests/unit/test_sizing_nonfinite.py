import math

import pytest
from pydantic import ValidationError

from services.portfolio.sizing import SizingInput


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capital", math.inf),
        ("capital", math.nan),
        ("max_fraction", math.inf),
        ("volatility", math.inf),
        ("target_volatility", math.nan),
        ("net_edge", math.nan),
        ("confidence", math.inf),
        ("market_probability", math.nan),
        ("model_probability", math.inf),
        ("hard_notional_cap", math.inf),
    ],
)
def test_sizing_input_rejects_nonfinite_values(field: str, value: float) -> None:
    payload = {
        "capital": 100_000.0,
        "hard_notional_cap": 10_000.0,
        field: value,
    }

    with pytest.raises(ValidationError):
        SizingInput(**payload)
