from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite, sqrt
from statistics import mean


@dataclass(frozen=True)
class EffectiveTrialReport:
    declared_trials: int
    implied_independent_trials: float
    effective_independent_trials: int
    average_pairwise_correlation: float
    pair_count: int
    method: str = "average_pairwise_correlation"


def _validate_trial_matrix(
    trial_returns: tuple[tuple[float, ...], ...],
) -> int:
    if not trial_returns:
        raise ValueError("at least one trial is required")

    sample_count = len(trial_returns[0])
    if sample_count < 3:
        raise ValueError("each trial requires at least three return observations")
    if any(len(values) != sample_count for values in trial_returns):
        raise ValueError("all trial return series must have equal length")
    if any(not isfinite(value) for values in trial_returns for value in values):
        raise ValueError("trial returns must be finite")
    return sample_count


def _pearson_correlation(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    left_mean = mean(left)
    right_mean = mean(right)
    left_centered = tuple(value - left_mean for value in left)
    right_centered = tuple(value - right_mean for value in right)
    left_sum_squares = sum(value * value for value in left_centered)
    right_sum_squares = sum(value * value for value in right_centered)
    if left_sum_squares <= 0.0 or right_sum_squares <= 0.0:
        raise ValueError(
            "effective trial correlation requires non-zero variance in every trial"
        )

    covariance_sum = sum(
        left_value * right_value
        for left_value, right_value in zip(left_centered, right_centered, strict=True)
    )
    correlation = covariance_sum / sqrt(left_sum_squares * right_sum_squares)
    if not isfinite(correlation):
        raise ValueError("trial correlation must be finite")
    if abs(1.0 - correlation) <= 1e-12:
        return 1.0
    if abs(-1.0 - correlation) <= 1e-12:
        return -1.0
    return min(max(correlation, -1.0), 1.0)


def effective_number_of_trials(
    trial_returns: tuple[tuple[float, ...], ...],
) -> EffectiveTrialReport:
    """Estimate the effective independent search burden from correlated trials.

    The estimator follows the average-correlation implication used with the
    Deflated Sharpe Ratio: for ``M`` declared trials with average pairwise
    correlation ``rho``, the implied independent count is
    ``rho + (1 - rho) * M``. Negative average correlation cannot imply more
    independent trials than were actually run, so the result is clipped to
    ``[1, M]``. The integer count is rounded upward to keep the multiple-testing
    penalty conservative.
    """

    _validate_trial_matrix(trial_returns)
    declared_trials = len(trial_returns)
    if declared_trials == 1:
        return EffectiveTrialReport(
            declared_trials=1,
            implied_independent_trials=1.0,
            effective_independent_trials=1,
            average_pairwise_correlation=0.0,
            pair_count=0,
        )

    correlations: list[float] = []
    for left_index in range(declared_trials - 1):
        for right_index in range(left_index + 1, declared_trials):
            correlations.append(
                _pearson_correlation(
                    trial_returns[left_index],
                    trial_returns[right_index],
                )
            )

    average_correlation = mean(correlations)
    implied = average_correlation + (1.0 - average_correlation) * declared_trials
    implied = min(max(implied, 1.0), float(declared_trials))
    effective = min(max(ceil(implied - 1e-12), 1), declared_trials)

    return EffectiveTrialReport(
        declared_trials=declared_trials,
        implied_independent_trials=implied,
        effective_independent_trials=effective,
        average_pairwise_correlation=average_correlation,
        pair_count=len(correlations),
    )
