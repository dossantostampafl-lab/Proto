from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from random import Random


@dataclass(frozen=True)
class MonteCarloSummary:
    simulations: int
    path_length: int
    block_size: int
    seed: int
    median_terminal_return: float
    p05_terminal_return: float
    p95_terminal_return: float
    median_max_drawdown: float
    p95_max_drawdown: float
    probability_of_loss: float


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("percentile requires observations")
    ordered = sorted(values)
    position = quantile * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _terminal_and_drawdown(path: tuple[float, ...]) -> tuple[float, float]:
    equity = 1.0
    peak = 1.0
    worst_drawdown = 0.0
    for value in path:
        equity *= 1.0 + value
        peak = max(peak, equity)
        if peak > 0.0:
            worst_drawdown = max(worst_drawdown, (peak - equity) / peak)
    return equity - 1.0, worst_drawdown


def block_bootstrap_path(
    returns: tuple[float, ...],
    *,
    path_length: int,
    block_size: int,
    rng: Random,
) -> tuple[float, ...]:
    if not returns:
        raise ValueError("returns must not be empty")
    if any(not isfinite(value) or value <= -1.0 for value in returns):
        raise ValueError("returns must be finite and greater than -1")
    if path_length <= 0 or block_size <= 0:
        raise ValueError("path_length and block_size must be positive")
    if block_size > len(returns):
        raise ValueError("block_size must not exceed returns length")

    path: list[float] = []
    max_start = len(returns) - block_size
    while len(path) < path_length:
        start = rng.randint(0, max_start)
        path.extend(returns[start : start + block_size])
    return tuple(path[:path_length])


def monte_carlo_block_bootstrap(
    returns: tuple[float, ...],
    *,
    simulations: int = 1_000,
    path_length: int | None = None,
    block_size: int = 5,
    seed: int = 7,
) -> MonteCarloSummary:
    if simulations <= 0:
        raise ValueError("simulations must be positive")
    effective_length = len(returns) if path_length is None else path_length
    rng = Random(seed)
    terminal_returns: list[float] = []
    drawdowns: list[float] = []

    for _ in range(simulations):
        path = block_bootstrap_path(
            returns,
            path_length=effective_length,
            block_size=block_size,
            rng=rng,
        )
        terminal, drawdown = _terminal_and_drawdown(path)
        terminal_returns.append(terminal)
        drawdowns.append(drawdown)

    return MonteCarloSummary(
        simulations=simulations,
        path_length=effective_length,
        block_size=block_size,
        seed=seed,
        median_terminal_return=_percentile(terminal_returns, 0.50),
        p05_terminal_return=_percentile(terminal_returns, 0.05),
        p95_terminal_return=_percentile(terminal_returns, 0.95),
        median_max_drawdown=_percentile(drawdowns, 0.50),
        p95_max_drawdown=_percentile(drawdowns, 0.95),
        probability_of_loss=sum(value < 0.0 for value in terminal_returns) / simulations,
    )
