import pytest

from apps.api.app.calibration import (
    CalibrationObservation,
    brier_score,
    calibration_error,
    log_loss,
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


def test_empty_observation_set_is_rejected() -> None:
    with pytest.raises(ValueError):
        brier_score([])
