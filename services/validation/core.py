from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from statistics import mean, pstdev


@dataclass(frozen=True)
class PurgedFold:
    train_indices: tuple[int, ...]
    test_indices: tuple[int, ...]


@dataclass(frozen=True)
class PerformanceMetrics:
    sample_count: int
    cumulative_return: float
    mean_return: float
    volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    hit_rate: float
    profit_factor: float


@dataclass(frozen=True)
class ValidationReport:
    metrics: PerformanceMetrics
    positive_fold_fraction: float
    worst_fold_return: float
    median_fold_return: float
    robustness_score: float


def purged_walk_forward_splits(
    sample_count: int,
    *,
    train_size: int,
    test_size: int,
    embargo_size: int = 0,
    purge_size: int = 0,
    step_size: int | None = None,
) -> tuple[PurgedFold, ...]:
    if sample_count <= 0:
        raise ValueError("sample_count must be positive")
    if train_size <= 0 or test_size <= 0:
        raise ValueError("train_size and test_size must be positive")
    if embargo_size < 0 or purge_size < 0:
        raise ValueError("embargo_size and purge_size must be non-negative")
    step = test_size if step_size is None else step_size
    if step <= 0:
        raise ValueError("step_size must be positive")

    folds: list[PurgedFold] = []
    train_start = 0
    while True:
        train_end = train_start + train_size
        test_start = train_end + embargo_size
        test_end = test_start + test_size
        if test_end > sample_count:
            break

        purged_train_end = max(train_start, train_end - purge_size)
        train_indices = tuple(range(train_start, purged_train_end))
        test_indices = tuple(range(test_start, test_end))
        if not train_indices:
            raise ValueError("purge_size removes the entire training fold")

        folds.append(PurgedFold(train_indices=train_indices, test_indices=test_indices))
        train_start += step

    if not folds:
        raise ValueError("configuration produces no validation folds")
    return tuple(folds)


def _max_drawdown(returns: tuple[float, ...]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        if peak > 0.0:
            worst = max(worst, (peak - equity) / peak)
    return worst


def _profit_factor(returns: tuple[float, ...]) -> float:
    gross_profit = sum(value for value in returns if value > 0.0)
    gross_loss = -sum(value for value in returns if value < 0.0)
    if gross_loss == 0.0:
        return float("inf") if gross_profit > 0.0 else 0.0
    return gross_profit / gross_loss


def performance_metrics(returns: tuple[float, ...]) -> PerformanceMetrics:
    if not returns:
        raise ValueError("returns must not be empty")
    if any(not isfinite(value) or value <= -1.0 for value in returns):
        raise ValueError("returns must be finite and greater than -1")

    avg = mean(returns)
    vol = pstdev(returns) if len(returns) > 1 else 0.0
    downside = tuple(min(value, 0.0) for value in returns)
    downside_dev = sqrt(sum(value * value for value in downside) / len(downside))
    sharpe = avg / vol if vol > 0.0 else 0.0
    sortino = avg / downside_dev if downside_dev > 0.0 else (float("inf") if avg > 0.0 else 0.0)

    equity = 1.0
    for value in returns:
        equity *= 1.0 + value

    return PerformanceMetrics(
        sample_count=len(returns),
        cumulative_return=equity - 1.0,
        mean_return=avg,
        volatility=vol,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=_max_drawdown(returns),
        hit_rate=sum(1 for value in returns if value > 0.0) / len(returns),
        profit_factor=_profit_factor(returns),
    )


def validation_report(
    returns: tuple[float, ...],
    folds: tuple[PurgedFold, ...],
) -> ValidationReport:
    metrics = performance_metrics(returns)
    fold_returns: list[float] = []
    for fold in folds:
        if not fold.test_indices:
            continue
        if fold.test_indices[-1] >= len(returns):
            raise ValueError("fold test index exceeds returns length")
        equity = 1.0
        for index in fold.test_indices:
            equity *= 1.0 + returns[index]
        fold_returns.append(equity - 1.0)

    if not fold_returns:
        raise ValueError("folds must contain test observations")

    ordered = sorted(fold_returns)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        median_fold = ordered[midpoint]
    else:
        median_fold = (ordered[midpoint - 1] + ordered[midpoint]) / 2.0

    positive_fraction = sum(1 for value in fold_returns if value > 0.0) / len(fold_returns)
    drawdown_component = max(0.0, 1.0 - min(metrics.max_drawdown, 1.0))
    consistency_component = max(0.0, min(1.0, positive_fraction))
    return_component = 1.0 if median_fold > 0.0 else 0.0
    robustness = 0.50 * consistency_component + 0.30 * drawdown_component + 0.20 * return_component

    return ValidationReport(
        metrics=metrics,
        positive_fold_fraction=positive_fraction,
        worst_fold_return=min(fold_returns),
        median_fold_return=median_fold,
        robustness_score=robustness,
    )
