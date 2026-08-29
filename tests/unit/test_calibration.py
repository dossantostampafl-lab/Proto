import pytest

from apps.api.app.calibration import (
    CalibrationObservation,
    brier_score,
    calibration_error,
    log_loss,
    reliability_curve,
)


def test_perfect_calibration_has_zero_brier_score() -> None:
    observations = [
        CalibrationObservation(probability=1.0, outcome=1),
        CalibrationObservation(probability=0.0, outcome=0),
    ]
    assert brier_score(observations) == 0.0


def test_log_loss_rewards_better_probabilities() -> None:
    good = [
        CalibrationObservation(probability=0.9, outcome=1),
        CalibrationObservation(probability=0.1, outcome=0),
    ]
    poor = [
        CalibrationObservation(probability=0.6, outcome=1),
        CalibrationObservation(probability=0.4, outcome=0),
    ]
    assert log_loss(good) < log_loss(poor)


def test_calibration_error_is_bounded() -> None:
    observations = [
        CalibrationObservation(probability=0.2, outcome=0),
        CalibrationObservation(probability=0.8, outcome=1),
        CalibrationObservation(probability=0.7, outcome=1),
    ]
    result = calibration_error(observations, bins=5)
    assert 0.0 <= result <= 1.0


def test_reliability_curve_returns_only_populated_buckets() -> None:
    observations = [
        CalibrationObservation(probability=0.1, outcome=0),
        CalibrationObservation(probability=0.2, outcome=1),
        CalibrationObservation(probability=0.8, outcome=1),
    ]
    curve = reliability_curve(observations, bins=5)

    assert sum(bucket.count for bucket in curve) == len(observations)
    assert all(bucket.count > 0 for bucket in curve)
    assert all(0.0 <= bucket.mean_prediction <= 1.0 for bucket in curve)
    assert all(0.0 <= bucket.observed_frequency <= 1.0 for bucket in curve)
    assert all(0.0 <= bucket.absolute_gap <= 1.0 for bucket in curve)


def test_empty_observation_set_is_rejected() -> None:
    with pytest.raises(ValueError):
        brier_score([])


def test_reliability_curve_rejects_invalid_bin_count() -> None:
    observations = [CalibrationObservation(probability=0.5, outcome=1)]
    with pytest.raises(ValueError, match="bins"):
        reliability_curve(observations, bins=1)
