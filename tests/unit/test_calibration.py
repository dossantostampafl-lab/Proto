from math import isclose

from services.quant.calibration import CalibrationBatch, score_calibration


def test_calibration_metrics_are_deterministic() -> None:
    batch = CalibrationBatch(
        observations=[
            {"predicted_probability": 0.8, "outcome": 1},
            {"predicted_probability": 0.3, "outcome": 0},
        ]
    )

    metrics = score_calibration(batch)

    assert metrics.count == 2
    assert metrics.brier_score == 0.065
    assert isclose(metrics.log_loss, 0.2899092476, rel_tol=1e-9)
    assert metrics.mean_prediction == 0.55
    assert metrics.observed_frequency == 0.5
    assert metrics.calibration_bias == 0.05


def test_calibration_handles_boundary_probabilities() -> None:
    batch = CalibrationBatch(
        observations=[
            {"predicted_probability": 1.0, "outcome": 1},
            {"predicted_probability": 0.0, "outcome": 0},
        ]
    )

    metrics = score_calibration(batch)

    assert metrics.brier_score == 0.0
    assert metrics.log_loss >= 0.0
    assert metrics.calibration_bias == 0.0
