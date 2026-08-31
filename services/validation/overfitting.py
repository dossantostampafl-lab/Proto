from __future__ import annotations

from itertools import combinations
from math import erf, exp, log, pi, sqrt
from statistics import mean, pstdev

from .core import performance_metrics

_EULER_GAMMA = 0.5772156649015329


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def _sample_moment(values: tuple[float, ...], order: int) -> float:
    avg = mean(values)
    return sum((value - avg) ** order for value in values) / len(values)


def deflated_sharpe_ratio(
    returns: tuple[float, ...],
    *,
    trials: int,
) -> float:
    if trials <= 0:
        raise ValueError("trials must be positive")
    if len(returns) < 3:
        raise ValueError("at least three returns are required")

    metrics = performance_metrics(returns)
    volatility = pstdev(returns)
    if volatility == 0.0:
        return 0.0

    m3 = _sample_moment(returns, 3)
    m4 = _sample_moment(returns, 4)
    skew = m3 / volatility**3
    kurtosis = m4 / volatility**4

    if trials == 1:
        expected_max_sharpe = 0.0
    else:
        log_trials = log(float(trials))
        first = (1.0 - _EULER_GAMMA) * sqrt(2.0 * log_trials)
        second = _EULER_GAMMA / sqrt(2.0 * log_trials)
        expected_max_sharpe = first + second

    sharpe = metrics.sharpe
    variance_term = 1.0 - skew * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe**2
    if variance_term <= 0.0:
        return 0.0

    statistic = (sharpe - expected_max_sharpe) * sqrt(len(returns) - 1) / sqrt(variance_term)
    return min(max(_normal_cdf(statistic), 0.0), 1.0)


def _strategy_score(returns: tuple[float, ...], indices: tuple[int, ...]) -> float:
    subset = tuple(returns[index] for index in indices)
    if not subset:
        raise ValueError("strategy score requires observations")
    return performance_metrics(subset).sharpe


def probability_of_backtest_overfitting(
    strategy_returns: tuple[tuple[float, ...], ...],
    *,
    segments: int = 8,
) -> float:
    if len(strategy_returns) < 2:
        raise ValueError("at least two strategies are required")
    sample_count = len(strategy_returns[0])
    if sample_count == 0 or any(len(values) != sample_count for values in strategy_returns):
        raise ValueError("all strategies must have the same non-empty sample count")
    if segments < 4 or segments % 2 != 0:
        raise ValueError("segments must be an even integer >= 4")
    if sample_count < segments or sample_count % segments != 0:
        raise ValueError("sample_count must be divisible by segments")

    segment_size = sample_count // segments
    segment_indices = tuple(
        tuple(range(segment * segment_size, (segment + 1) * segment_size))
        for segment in range(segments)
    )
    half = segments // 2
    negative_logits = 0
    evaluated = 0

    for train_segments in combinations(range(segments), half):
        train_set = set(train_segments)
        train_indices = tuple(index for seg in train_segments for index in segment_indices[seg])
        test_indices = tuple(
            index
            for seg in range(segments)
            if seg not in train_set
            for index in segment_indices[seg]
        )

        train_scores = tuple(_strategy_score(values, train_indices) for values in strategy_returns)
        best_strategy = max(range(len(train_scores)), key=train_scores.__getitem__)
        test_scores = tuple(_strategy_score(values, test_indices) for values in strategy_returns)
        ranked = sorted(range(len(test_scores)), key=test_scores.__getitem__)
        rank = ranked.index(best_strategy) + 1
        relative_rank = rank / (len(test_scores) + 1.0)
        logit = log(relative_rank / (1.0 - relative_rank))
        if logit <= 0.0:
            negative_logits += 1
        evaluated += 1

    if evaluated == 0:
        raise ValueError("configuration produced no CSCV splits")
    return negative_logits / evaluated


def expected_max_sharpe_under_null(trials: int) -> float:
    if trials <= 0:
        raise ValueError("trials must be positive")
    if trials == 1:
        return 0.0
    log_trials = log(float(trials))
    return (
        (1.0 - _EULER_GAMMA) * sqrt(2.0 * log_trials)
        + _EULER_GAMMA / sqrt(2.0 * log_trials)
    )
