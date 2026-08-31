from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from .core import PerformanceMetrics, performance_metrics


@dataclass(frozen=True)
class RegimePerformance:
    regime: str
    metrics: PerformanceMetrics


@dataclass(frozen=True)
class RegimeRobustnessReport:
    regimes: tuple[RegimePerformance, ...]
    profitable_regime_fraction: float
    worst_regime_return: float
    return_dispersion: float
    robustness_score: float


@dataclass(frozen=True)
class ParameterPoint:
    parameter: float
    score: float


@dataclass(frozen=True)
class ParameterStabilityReport:
    best_parameter: float
    best_score: float
    plateau_fraction: float
    local_neighbor_fraction: float
    stability_score: float


def regime_robustness(
    returns: tuple[float, ...],
    regimes: tuple[str, ...],
) -> RegimeRobustnessReport:
    if not returns or len(returns) != len(regimes):
        raise ValueError("returns and regimes must have the same non-zero length")

    grouped: dict[str, list[float]] = {}
    for value, regime in zip(returns, regimes, strict=True):
        normalized = regime.strip().upper()
        if not normalized:
            raise ValueError("regime labels must not be blank")
        grouped.setdefault(normalized, []).append(value)

    reports = tuple(
        RegimePerformance(regime=regime, metrics=performance_metrics(tuple(values)))
        for regime, values in sorted(grouped.items())
    )
    cumulative_returns = tuple(item.metrics.cumulative_return for item in reports)
    profitable_fraction = sum(value > 0.0 for value in cumulative_returns) / len(cumulative_returns)
    average_return = mean(cumulative_returns)
    dispersion = mean(abs(value - average_return) for value in cumulative_returns)
    dispersion_penalty = min(max(dispersion, 0.0), 1.0)
    robustness = 0.70 * profitable_fraction + 0.30 * (1.0 - dispersion_penalty)

    return RegimeRobustnessReport(
        regimes=reports,
        profitable_regime_fraction=profitable_fraction,
        worst_regime_return=min(cumulative_returns),
        return_dispersion=dispersion,
        robustness_score=max(0.0, min(1.0, robustness)),
    )


def parameter_stability(
    points: tuple[ParameterPoint, ...],
    *,
    relative_tolerance: float = 0.10,
) -> ParameterStabilityReport:
    if len(points) < 3:
        raise ValueError("at least three parameter points are required")
    if not 0.0 <= relative_tolerance <= 1.0:
        raise ValueError("relative_tolerance must be between 0 and 1")

    ordered = tuple(sorted(points, key=lambda item: item.parameter))
    if len({item.parameter for item in ordered}) != len(ordered):
        raise ValueError("parameter values must be unique")

    best_index = max(range(len(ordered)), key=lambda index: ordered[index].score)
    best = ordered[best_index]
    tolerance = abs(best.score) * relative_tolerance
    threshold = best.score - tolerance
    plateau_fraction = sum(item.score >= threshold for item in ordered) / len(ordered)

    neighbor_indices = tuple(
        index
        for index in (best_index - 1, best_index + 1)
        if 0 <= index < len(ordered)
    )
    local_neighbor_fraction = (
        sum(ordered[index].score >= threshold for index in neighbor_indices) / len(neighbor_indices)
        if neighbor_indices
        else 0.0
    )
    stability = 0.60 * plateau_fraction + 0.40 * local_neighbor_fraction

    return ParameterStabilityReport(
        best_parameter=best.parameter,
        best_score=best.score,
        plateau_fraction=plateau_fraction,
        local_neighbor_fraction=local_neighbor_fraction,
        stability_score=stability,
    )
