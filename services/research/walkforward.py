"""Walk-forward evaluation: parameters chosen on train windows, judged on test windows only.

Windows roll forward in time: ``[train | test]`` with the test immediately after its train and
consecutive tests contiguous and non-overlapping, so every test bar is judged by parameters
chosen without seeing it. Indicators are causal (bar i uses bars 0..i), so computing a plan on
the whole series leaks nothing: a test decision at bar i only reads bars <= i.

Selection rule per window: among the configurations with at least ``min_trades`` train trades,
pick the best train Sharpe; if that configuration did not make money on train, **abstain**
(stay flat) for the test window. Test windows chain: each starts with the equity the previous
one ended with, as an agent would.

``deflated_sharpe`` applies Bailey & Lopez de Prado's Deflated Sharpe Ratio: the probability
that the observed Sharpe beats the best Sharpe expected from ``trials`` unskilled
configurations, given the dispersion of the Sharpes that were tried.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any

from services.research.backtest import (
    DAY_MS,
    CostModel,
    Result,
    SizingModel,
    buy_and_hold,
    daily_returns,
    metrics,
    run,
)
from services.research.data import Bar
from services.research.strategies import Plan

EULER_GAMMA = 0.5772156649015329


@dataclass(frozen=True)
class Window:
    train_start: int
    train_end: int  # exclusive; == test_start
    test_start: int
    test_end: int  # exclusive


def windows(
    times: Sequence[int], *, train_days: float, test_days: float, step_days: float | None = None
) -> list[Window]:
    """Rolling windows over bar open times (ms). Train always ends where its test begins."""

    if not times:
        return []
    step_days = step_days or test_days
    first, last = times[0], times[-1]
    spacing = times[1] - times[0] if len(times) > 1 else 1
    out: list[Window] = []
    k = 0
    while True:
        train_from = first + int(k * step_days * DAY_MS)
        test_from = train_from + int(train_days * DAY_MS)
        test_to = test_from + int(test_days * DAY_MS)
        if test_to > last + spacing:
            break
        idx = [_index(times, t) for t in (train_from, test_from, test_to)]
        if idx[0] < idx[1] < idx[2]:
            out.append(Window(idx[0], idx[1], idx[1], idx[2]))
        k += 1
    return out


def _index(times: Sequence[int], t: int) -> int:
    from bisect import bisect_left

    return bisect_left(times, t)


def chain(results: Sequence[Result], start_equity: float) -> Result:
    """Concatenate consecutive window results into one equity path."""

    out = Result(start_equity=start_equity, equity=[], times=[])
    for r in results:
        out.equity.extend(r.equity)
        out.times.extend(r.times)
        out.trades.extend(r.trades)
        out.fees += r.fees
        out.slippage_cost += r.slippage_cost
        out.bars_in_market += r.bars_in_market
        out.eliminated = out.eliminated or r.eliminated
    return out


def _flat(bars: Sequence[Bar], w: Window, equity: float) -> Result:
    return Result(
        start_equity=equity,
        equity=[equity] * (w.test_end - w.test_start),
        times=[b.open_time for b in bars[w.test_start : w.test_end]],
    )


@dataclass
class WalkForward:
    oos: Result
    chosen: list[dict[str, Any] | None]  # per window: the config spec, or None (abstained)
    per_window: list[dict[str, float]]
    fixed_oos: list[Result] = field(default_factory=list)  # each config, chained on tests


def walk_forward(
    bars: Sequence[Bar],
    configs: Sequence[Any],
    wins: Sequence[Window],
    *,
    costs: CostModel,
    sizing: SizingModel,
    min_trades: int = 3,
    plans: Sequence[Plan] | None = None,
) -> WalkForward:
    plans = list(plans) if plans is not None else [c.plan(bars) for c in configs]
    start_equity = sizing.agent_capital_brl / sizing.fx_brl_per_quote
    equity = start_equity
    tests: list[Result] = []
    chosen: list[dict[str, Any] | None] = []
    per_window: list[dict[str, float]] = []
    fixed_equity = [start_equity] * len(configs)
    fixed_parts: list[list[Result]] = [[] for _ in configs]
    for w in wins:
        best, best_score, best_ret = None, -math.inf, 0.0
        for k, plan in enumerate(plans):
            train = run(
                bars, plan, start=w.train_start, end=w.train_end, costs=costs, sizing=sizing
            )
            m = metrics(train)
            if m["trades"] < min_trades:
                continue
            if m["sharpe"] > best_score:
                best, best_score, best_ret = k, m["sharpe"], m["total_return"]
        if best is None or best_ret <= 0:
            test = _flat(bars, w, equity)
            chosen.append(None)
        else:
            test = run(
                bars,
                plans[best],
                start=w.test_start,
                end=w.test_end,
                costs=costs,
                sizing=sizing,
                start_equity=equity,
            )
            chosen.append(configs[best].describe())
        per_window.append({"return": test.end_equity / equity - 1, "trades": len(test.trades)})
        equity = test.end_equity
        tests.append(test)
        for k, plan in enumerate(plans):
            r = run(
                bars,
                plan,
                start=w.test_start,
                end=w.test_end,
                costs=costs,
                sizing=sizing,
                start_equity=fixed_equity[k],
            )
            fixed_equity[k] = r.end_equity
            fixed_parts[k].append(r)
    return WalkForward(
        oos=chain(tests, start_equity),
        chosen=chosen,
        per_window=per_window,
        fixed_oos=[chain(parts, start_equity) for parts in fixed_parts],
    )


def benchmark_oos(
    bars: Sequence[Bar], wins: Sequence[Window], *, costs: CostModel, sizing: SizingModel
) -> Result:
    """Buy and hold over the whole tested span (first test open to last test close)."""

    return buy_and_hold(
        bars, start=wins[0].test_start, end=wins[-1].test_end, costs=costs, sizing=sizing
    )


def fixed_oos(
    bars: Sequence[Bar],
    plan: Plan,
    wins: Sequence[Window],
    *,
    costs: CostModel,
    sizing: SizingModel,
) -> Result:
    """One fixed configuration traded on every test window, chained (no selection)."""

    start_equity = sizing.agent_capital_brl / sizing.fx_brl_per_quote
    equity, parts = start_equity, []
    for w in wins:
        r = run(
            bars,
            plan,
            start=w.test_start,
            end=w.test_end,
            costs=costs,
            sizing=sizing,
            start_equity=equity,
        )
        equity = r.end_equity
        parts.append(r)
    return chain(parts, start_equity)


# --- multiple-testing haircut ---------------------------------------------------------------


def _moments(xs: Sequence[float]) -> tuple[float, float, float, float]:
    n = len(xs)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n
    if var <= 0:
        return mean, 0.0, 0.0, 3.0
    sd = math.sqrt(var)
    skew = sum(((x - mean) / sd) ** 3 for x in xs) / n
    kurt = sum(((x - mean) / sd) ** 4 for x in xs) / n
    return mean, sd, skew, kurt


def expected_max_sharpe(trials: int, sharpe_variance: float) -> float:
    """Expected maximum of ``trials`` Sharpe ratios of zero-skill strategies (per period)."""

    if trials <= 1 or sharpe_variance <= 0:
        return 0.0
    z = NormalDist()
    return math.sqrt(sharpe_variance) * (
        (1 - EULER_GAMMA) * z.inv_cdf(1 - 1 / trials)
        + EULER_GAMMA * z.inv_cdf(1 - 1 / (trials * math.e))
    )


def deflated_sharpe(returns: Sequence[float], trials: int, sharpe_variance: float) -> float:
    """Probability (0..1) that the per-period Sharpe of ``returns`` is real after ``trials``."""

    if len(returns) < 3:
        return 0.0
    mean, sd, skew, kurt = _moments(returns)
    if sd == 0:
        return 0.0
    sr = mean / sd
    sr0 = expected_max_sharpe(trials, sharpe_variance)
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom <= 0:
        return 0.0
    return NormalDist().cdf((sr - sr0) * math.sqrt(len(returns) - 1) / math.sqrt(denom))


def per_period_sharpe(result: Result) -> float:
    rets = daily_returns(result.equity, result.times, result.start_equity)
    if len(rets) < 2:
        return 0.0
    mean, sd, _, _ = _moments(rets)
    return mean / sd if sd > 0 else 0.0
