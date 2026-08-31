from services.validation import (
    performance_metrics,
    purged_walk_forward_splits,
    validation_report,
)


def test_purged_walk_forward_respects_embargo_and_purge() -> None:
    folds = purged_walk_forward_splits(
        30,
        train_size=10,
        test_size=5,
        purge_size=2,
        embargo_size=1,
        step_size=5,
    )

    first = folds[0]
    assert first.train_indices == tuple(range(0, 8))
    assert first.test_indices == tuple(range(11, 16))
    assert max(first.train_indices) < min(first.test_indices)


def test_performance_metrics_capture_drawdown_and_hit_rate() -> None:
    metrics = performance_metrics((0.10, -0.05, 0.02, -0.01, 0.03))

    assert metrics.sample_count == 5
    assert 0.0 < metrics.hit_rate < 1.0
    assert metrics.max_drawdown > 0.0
    assert metrics.profit_factor > 1.0


def test_validation_report_rewards_consistent_positive_folds() -> None:
    returns = (
        0.01,
        0.02,
        -0.005,
        0.01,
        0.015,
        0.01,
        0.005,
        -0.002,
        0.012,
        0.008,
        0.011,
        0.006,
        -0.001,
        0.009,
        0.007,
        0.010,
        0.004,
        -0.002,
        0.008,
        0.006,
    )
    folds = purged_walk_forward_splits(
        len(returns),
        train_size=8,
        test_size=4,
        purge_size=1,
        embargo_size=1,
        step_size=4,
    )
    report = validation_report(returns, folds)

    assert report.positive_fold_fraction == 1.0
    assert report.median_fold_return > 0.0
    assert report.robustness_score > 0.8


def test_validation_report_rejects_fold_outside_returns() -> None:
    folds = purged_walk_forward_splits(12, train_size=6, test_size=3)

    try:
        validation_report((0.01,) * 5, folds)
    except ValueError as exc:
        assert "exceeds returns length" in str(exc)
    else:
        raise AssertionError("expected out-of-range fold to be rejected")
