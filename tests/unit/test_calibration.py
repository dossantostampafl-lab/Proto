import pytest

from services.quant.calibration import calibration_report


def test_perfect_calibration_has_zero_brier_score() -> None:
    report = calibration_report([(1.0, 1), (0.0, 0)])
    assert report.brier_score == 0.0


def test_log_loss_rewards_better_probabilities() -> None:
    good = calibration_report([(0.9, 1), (0.1, 0)])
    poor = calibration_report([(0.6, 1), (0.4, 0)])
    assert good.log_loss < poor.log_loss


def test_calibration_error_is_bounded() -> None:
    report = calibration_report([(0.2, 0), (0.8, 1), (0.7, 1)], bin_count=5)
    assert 0.0 <= report.expected_calibration_error <= 1.0


def test_reliability_curve_returns_only_populated_buckets() -> None:
    report = calibration_report([(0.1, 0), (0.2, 1), (0.8, 1)], bin_count=5)
    curve = [bucket for bucket in report.bins if bucket.count > 0]

    assert sum(bucket.count for bucket in curve) == report.count
    assert all(bucket.count > 0 for bucket in curve)
    assert all(
        bucket.mean_probability is not None and 0.0 <= bucket.mean_probability <= 1.0
        for bucket in curve
    )
    assert all(
        bucket.observed_frequency is not None and 0.0 <= bucket.observed_frequency <= 1.0
        for bucket in curve
    )
    assert all(
        bucket.calibration_error is not None and 0.0 <= bucket.calibration_error <= 1.0
        for bucket in curve
    )


def test_empty_observation_set_is_rejected() -> None:
    with pytest.raises(ValueError):
        calibration_report([])


def test_reliability_curve_rejects_invalid_bin_count() -> None:
    with pytest.raises(ValueError, match="bin_count"):
        calibration_report([(0.5, 1)], bin_count=1)
