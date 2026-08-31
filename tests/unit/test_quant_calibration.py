from __future__ import annotations

import math

import pytest

from services.quant.calibration import calibration_report


def test_perfect_binary_forecasts_have_zero_brier_and_calibration_error() -> None:
    report = calibration_report([(0.0, 0), (1.0, 1)], bin_count=2)

    assert report.count == 2
    assert report.brier_score == 0.0
    assert report.expected_calibration_error == 0.0
    assert report.maximum_calibration_error == 0.0
    assert report.log_loss < 1e-9


def test_report_computes_known_brier_and_log_loss() -> None:
    samples = [(0.25, 0), (0.75, 1), (0.75, 0), (0.25, 1)]
    report = calibration_report(samples, bin_count=2)

    expected_brier = (0.25**2 + 0.25**2 + 0.75**2 + 0.75**2) / 4
    expected_log_loss = -(2 * math.log(0.75) + 2 * math.log(0.25)) / 4
    assert report.brier_score == pytest.approx(expected_brier)
    assert report.log_loss == pytest.approx(expected_log_loss)
    assert report.expected_calibration_error == pytest.approx(0.25)
    assert report.maximum_calibration_error == pytest.approx(0.25)


def test_bins_include_empty_ranges_without_fake_statistics() -> None:
    report = calibration_report([(0.05, 0), (0.95, 1)], bin_count=5)

    assert len(report.bins) == 5
    empty = report.bins[2]
    assert empty.count == 0
    assert empty.mean_probability is None
    assert empty.observed_frequency is None
    assert empty.calibration_error is None


@pytest.mark.parametrize(
    "samples",
    [[], [(-0.1, 0)], [(1.1, 1)], [(0.5, 2)]],
)
def test_invalid_samples_are_rejected(samples) -> None:
    with pytest.raises(ValueError):
        calibration_report(samples)


def test_invalid_calibration_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        calibration_report([(0.5, 1)], bin_count=1)
    with pytest.raises(ValueError):
        calibration_report([(0.5, 1)], epsilon=0.5)
