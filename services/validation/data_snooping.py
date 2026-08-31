from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, log, sqrt
from random import Random
from statistics import mean


@dataclass(frozen=True)
class SuperiorPredictiveAbilityReport:
    strategy_count: int
    sample_count: int
    best_strategy_index: int
    best_mean_excess_return: float
    reality_check_p_value: float
    spa_consistent_p_value: float
    spa_lower_p_value: float
    spa_upper_p_value: float
    consistent_strategy_count: int
    bootstrap_simulations: int
    block_size: int
    seed: int
    bootstrap: str = "stationary"


def _validate_inputs(
    strategy_returns: tuple[tuple[float, ...], ...],
    benchmark_returns: tuple[float, ...],
    *,
    simulations: int,
    block_size: int,
) -> int:
    if len(strategy_returns) < 2:
        raise ValueError("at least two strategies are required")
    sample_count = len(benchmark_returns)
    if sample_count < 4:
        raise ValueError("at least four benchmark observations are required")
    if any(len(values) != sample_count for values in strategy_returns):
        raise ValueError("strategy and benchmark series must have equal length")
    if not all(isfinite(value) for value in benchmark_returns):
        raise ValueError("benchmark returns must be finite")
    if not all(
        isfinite(value)
        for values in strategy_returns
        for value in values
    ):
        raise ValueError("strategy returns must be finite")
    if simulations < 100:
        raise ValueError("simulations must be at least 100")
    if block_size <= 0 or block_size > sample_count:
        raise ValueError("block_size must be between 1 and sample count")
    return sample_count


def _stationary_bootstrap_indices(
    sample_count: int,
    *,
    restart_probability: float,
    rng: Random,
) -> tuple[int, ...]:
    indices = [rng.randrange(sample_count)]
    while len(indices) < sample_count:
        if rng.random() < restart_probability:
            indices.append(rng.randrange(sample_count))
        else:
            indices.append((indices[-1] + 1) % sample_count)
    return tuple(indices)


def _long_run_variance(
    values: tuple[float, ...],
    *,
    block_size: int,
) -> float:
    sample_count = len(values)
    average = mean(values)
    demeaned = tuple(value - average for value in values)
    variance = sum(value * value for value in demeaned) / sample_count
    restart_probability = 1.0 / block_size

    for lag in range(1, sample_count):
        persistence = 1.0 - restart_probability
        kappa = (
            (1.0 - lag / sample_count) * persistence**lag
            + (lag / sample_count) * persistence ** (sample_count - lag)
        )
        covariance = sum(
            demeaned[index] * demeaned[index + lag]
            for index in range(sample_count - lag)
        ) / sample_count
        variance += 2.0 * kappa * covariance

    return max(variance, 0.0)


def _bootstrap_p_value(exceedances: int, simulations: int) -> float:
    return (exceedances + 1.0) / (simulations + 1.0)


def superior_predictive_ability(
    strategy_returns: tuple[tuple[float, ...], ...],
    benchmark_returns: tuple[float, ...],
    *,
    simulations: int = 1_000,
    block_size: int = 5,
    seed: int = 7,
) -> SuperiorPredictiveAbilityReport:
    """Test a searched strategy family against an explicit benchmark.

    Returns White-style Reality Check and Hansen SPA p-values. Strategy and
    benchmark inputs are returns, so positive excess return means the candidate
    outperformed the benchmark. A stationary bootstrap resamples one common
    time index path for the whole family, preserving cross-strategy dependence.
    """

    sample_count = _validate_inputs(
        strategy_returns,
        benchmark_returns,
        simulations=simulations,
        block_size=block_size,
    )
    excess_returns = tuple(
        tuple(
            strategy[index] - benchmark_returns[index]
            for index in range(sample_count)
        )
        for strategy in strategy_returns
    )
    excess_means = tuple(mean(values) for values in excess_returns)
    best_strategy_index = max(
        range(len(excess_means)),
        key=excess_means.__getitem__,
    )
    observed_best_mean = excess_means[best_strategy_index]

    variances = tuple(
        _long_run_variance(values, block_size=block_size)
        for values in excess_returns
    )
    log_log_sample = log(log(float(sample_count)))
    relevance_thresholds = tuple(
        -sqrt((variance / sample_count) * 2.0 * log_log_sample)
        for variance in variances
    )
    consistent_columns = tuple(
        average >= threshold
        for average, threshold in zip(
            excess_means,
            relevance_thresholds,
            strict=True,
        )
    )

    lower_center = tuple(
        average if average >= 0.0 else 0.0
        for average in excess_means
    )
    consistent_center = tuple(
        average if relevant else 0.0
        for average, relevant in zip(
            excess_means,
            consistent_columns,
            strict=True,
        )
    )
    upper_center = excess_means

    exceedances = [0, 0, 0]
    rng = Random(seed)
    restart_probability = 1.0 / block_size
    for _ in range(simulations):
        indices = _stationary_bootstrap_indices(
            sample_count,
            restart_probability=restart_probability,
            rng=rng,
        )
        sampled_means = tuple(
            sum(values[index] for index in indices) / sample_count
            for values in excess_returns
        )
        for position, center in enumerate(
            (lower_center, consistent_center, upper_center)
        ):
            simulated_best = max(
                sampled - recenter
                for sampled, recenter in zip(
                    sampled_means,
                    center,
                    strict=True,
                )
            )
            if simulated_best > observed_best_mean:
                exceedances[position] += 1

    lower_p_value = _bootstrap_p_value(exceedances[0], simulations)
    consistent_p_value = _bootstrap_p_value(exceedances[1], simulations)
    upper_p_value = _bootstrap_p_value(exceedances[2], simulations)

    return SuperiorPredictiveAbilityReport(
        strategy_count=len(strategy_returns),
        sample_count=sample_count,
        best_strategy_index=best_strategy_index,
        best_mean_excess_return=observed_best_mean,
        reality_check_p_value=upper_p_value,
        spa_consistent_p_value=consistent_p_value,
        spa_lower_p_value=lower_p_value,
        spa_upper_p_value=upper_p_value,
        consistent_strategy_count=sum(consistent_columns),
        bootstrap_simulations=simulations,
        block_size=block_size,
        seed=seed,
    )
