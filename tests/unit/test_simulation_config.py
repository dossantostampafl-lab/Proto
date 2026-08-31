import math

import pytest

from apps.api.app.models import Side
from apps.api.app.simulation import PaperSimulator, SimulationConfig


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fee_bps", math.nan),
        ("fee_bps", math.inf),
        ("base_slippage_bps", math.nan),
        ("base_slippage_bps", math.inf),
        ("latency_ms", math.nan),
        ("latency_ms", math.inf),
        ("latency_slippage_bps_per_100ms", math.nan),
        ("latency_slippage_bps_per_100ms", math.inf),
        ("tick_size", math.nan),
        ("tick_size", math.inf),
        ("depth_impact_bps_at_full_book", math.nan),
        ("depth_impact_bps_at_full_book", math.inf),
        ("depth_impact_exponent", math.nan),
        ("depth_impact_exponent", math.inf),
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
        ("latency_ms", -0.1),
        ("latency_slippage_bps_per_100ms", -0.1),
        ("tick_size", 0.0),
        ("tick_size", -0.01),
        ("depth_impact_bps_at_full_book", -0.1),
        ("depth_impact_exponent", 0.0),
        ("depth_impact_exponent", -1.0),
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


def test_default_execution_friction_is_explicit_and_nonzero() -> None:
    config = SimulationConfig()

    assert config.fee_bps > 0.0
    assert config.base_slippage_bps > 0.0
    assert config.latency_ms > 0.0
    assert config.latency_slippage_bps_per_100ms > 0.0
    assert config.tick_size > 0.0
    assert config.depth_impact_bps_at_full_book > 0.0
    assert config.depth_impact_exponent > 1.0


def test_tick_grid_rounding_is_adverse_by_side() -> None:
    simulator = PaperSimulator(SimulationConfig(tick_size=0.05))

    assert simulator._price_on_grid(100.021, Side.BUY) == pytest.approx(100.05)
    assert simulator._price_on_grid(100.029, Side.SELL) == pytest.approx(100.0)


def test_depth_impact_is_monotonic_and_convex() -> None:
    simulator = PaperSimulator(
        SimulationConfig(
            depth_impact_bps_at_full_book=8.0,
            depth_impact_exponent=2.0,
        )
    )

    quarter = simulator._depth_impact_bps(order_quantity=25.0, available_quantity=100.0)
    half = simulator._depth_impact_bps(order_quantity=50.0, available_quantity=100.0)
    full = simulator._depth_impact_bps(order_quantity=100.0, available_quantity=100.0)

    assert quarter == pytest.approx(0.5)
    assert half == pytest.approx(2.0)
    assert full == pytest.approx(8.0)
    assert half > quarter * 2.0


def test_zero_depth_produces_infinite_impact_and_fails_closed_upstream() -> None:
    simulator = PaperSimulator()

    assert math.isinf(simulator._depth_impact_bps(order_quantity=1.0, available_quantity=0.0))
