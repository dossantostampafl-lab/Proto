import math

import pytest

from apps.api.app.simulation import SimulationConfig


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fee_bps", math.nan),
        ("fee_bps", math.inf),
        ("base_slippage_bps", math.nan),
        ("base_slippage_bps", math.inf),
        ("max_snapshot_age_seconds", math.nan),
        ("max_snapshot_age_seconds", math.inf),
        ("max_future_skew_seconds", math.nan),
        ("max_future_skew_seconds", math.inf),
    ],
)
def test_simulation_config_rejects_nonfinite_values(field: str, value: float) -> None:
    with pytest.raises(ValueError, match="must be finite"):
        SimulationConfig(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fee_bps", -0.1),
        ("base_slippage_bps", -0.1),
        ("max_snapshot_age_seconds", 0.0),
        ("max_snapshot_age_seconds", -0.1),
        ("max_future_skew_seconds", -0.1),
    ],
)
def test_simulation_config_rejects_invalid_ranges(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        SimulationConfig(**{field: value})


def test_simulation_config_accepts_zero_future_skew() -> None:
    config = SimulationConfig(max_future_skew_seconds=0.0)

    assert config.max_future_skew_seconds == 0.0
