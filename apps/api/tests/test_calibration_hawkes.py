from math import isclose

import pytest

from proto_api.calibration import brier_score, expected_calibration_error, log_loss
from proto_api.hawkes import exponential_hawkes_state


def test_calibration_metrics_are_well_defined() -> None:
    probabilities = [0.1, 0.2, 0.8, 0.9]
    outcomes = [0, 0, 1, 1]

    assert isclose(brier_score(probabilities, outcomes), 0.025, rel_tol=1e-9)
    assert log_loss(probabilities, outcomes) > 0
    assert 0 <= expected_calibration_error(probabilities, outcomes, bucket_count=2) <= 1


def test_hawkes_intensity_increases_after_recent_events() -> None:
    calm = exponential_hawkes_state(now=10.0, event_times=[], mu=0.2, alpha=0.4, beta=1.0)
    active = exponential_hawkes_state(
        now=10.0,
        event_times=[9.9, 9.7],
        mu=0.2,
        alpha=0.4,
        beta=1.0,
    )

    assert active.current_intensity > calm.current_intensity
    assert 0 < active.event_probability < 1
    assert active.branching_ratio == 0.4


def test_hawkes_rejects_unstable_parameters() -> None:
    with pytest.raises(ValueError, match="unstable"):
        exponential_hawkes_state(
            now=1.0,
            event_times=[],
            mu=0.2,
            alpha=1.0,
            beta=1.0,
        )
