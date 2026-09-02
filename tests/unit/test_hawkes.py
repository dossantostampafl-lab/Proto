import math

import pytest

from services.hawkes.core import ExponentialHawkesEngine


def test_hawkes_baseline_without_events() -> None:
    engine = ExponentialHawkesEngine(mu=0.2, alpha=0.3, beta=1.0)

    estimate = engine.estimate(timestamp=10.0, horizon=1.0)

    assert estimate.baseline_intensity == 0.2
    assert estimate.current_intensity == 0.2
    assert estimate.excitation == 0.0
    assert estimate.branching_ratio == 0.3
    assert estimate.event_probability == pytest.approx(1.0 - math.exp(-0.2))


def test_hawkes_event_excitation_decays_over_time() -> None:
    engine = ExponentialHawkesEngine(mu=0.1, alpha=0.4, beta=1.0)
    engine.record(1.0)

    immediate = engine.estimate(timestamp=1.0)
    later = engine.estimate(timestamp=3.0)

    assert immediate.current_intensity == pytest.approx(0.5)
    assert later.current_intensity < immediate.current_intensity
    assert later.current_intensity > engine.mu


def test_hawkes_event_probability_integrates_decaying_excitation() -> None:
    engine = ExponentialHawkesEngine(mu=0.1, alpha=0.4, beta=2.0)
    engine.record(1.0)

    estimate = engine.estimate(timestamp=1.0, horizon=2.0)
    integrated_intensity = 0.1 * 2.0 + 0.4 * (1.0 - math.exp(-4.0)) / 2.0
    constant_intensity_approximation = 1.0 - math.exp(-0.5 * 2.0)

    assert estimate.event_probability == pytest.approx(
        1.0 - math.exp(-integrated_intensity)
    )
    assert estimate.event_probability < constant_intensity_approximation


def test_hawkes_rejects_unstable_parameters() -> None:
    with pytest.raises(ValueError, match="stable process"):
        ExponentialHawkesEngine(mu=0.1, alpha=1.0, beta=1.0)


def test_hawkes_rejects_out_of_order_events() -> None:
    engine = ExponentialHawkesEngine(mu=0.1, alpha=0.2, beta=1.0)
    engine.record(2.0)

    with pytest.raises(ValueError, match="non-decreasing"):
        engine.record(1.0)
