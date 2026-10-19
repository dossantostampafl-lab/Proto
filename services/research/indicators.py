"""Causal indicators (value at index i uses bars 0..i only). Pure Python, no numpy."""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right, insort
from collections import deque
from collections.abc import Sequence

from services.research.data import Bar

NAN = math.nan


def ema(values: Sequence[float], period: int) -> list[float]:
    """Exponential average seeded with the first value (as services.signals.proto_strategy)."""

    if not values:
        return []
    alpha = 2.0 / (period + 1)
    out = [float(values[0])]
    prev = out[0]
    for value in values[1:]:
        prev = alpha * value + (1 - alpha) * prev
        out.append(prev)
    return out


def sma(values: Sequence[float], period: int) -> list[float]:
    out = [NAN] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def rolling_std(values: Sequence[float], period: int) -> list[float]:
    """Population standard deviation over the last ``period`` values."""

    out = [NAN] * len(values)
    s = s2 = 0.0
    for i, value in enumerate(values):
        s += value
        s2 += value * value
        if i >= period:
            old = values[i - period]
            s -= old
            s2 -= old * old
        if i >= period - 1:
            mean = s / period
            out[i] = math.sqrt(max(s2 / period - mean * mean, 0.0))
    return out


def true_range(bars: Sequence[Bar]) -> list[float]:
    out = []
    prev_close = None
    for b in bars:
        if prev_close is None:
            out.append(b.high - b.low)
        else:
            out.append(max(b.high, prev_close) - min(b.low, prev_close))
        prev_close = b.close
    return out


def atr(bars: Sequence[Bar], period: int) -> list[float]:
    """Wilder's average true range; NaN until ``period`` bars exist."""

    tr = true_range(bars)
    out = [NAN] * len(bars)
    if len(bars) < period:
        return out
    value = sum(tr[:period]) / period
    out[period - 1] = value
    for i in range(period, len(bars)):
        value = (value * (period - 1) + tr[i]) / period
        out[i] = value
    return out


def rsi(closes: Sequence[float], period: int) -> list[float]:
    """Wilder's RSI; NaN until ``period`` changes exist."""

    out = [NAN] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period

    def value() -> float:
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    out[period] = value()
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = value()
    return out


def prior_max(values: Sequence[float], period: int) -> list[float]:
    """Max of the ``period`` values *before* i (excludes i itself)."""

    out = [NAN] * len(values)
    window: deque[int] = deque()
    for i in range(len(values)):
        while window and window[0] < i - period:
            window.popleft()
        if i >= period:
            out[i] = values[window[0]]
        while window and values[window[-1]] <= values[i]:
            window.pop()
        window.append(i)
    return out


def prior_min(values: Sequence[float], period: int) -> list[float]:
    neg = prior_max([-v for v in values], period)
    return [-v for v in neg]


def percentile_rank(values: Sequence[float], window: int) -> list[float]:
    """Fraction of the trailing ``window`` values (including i) that are <= value[i]."""

    out = [NAN] * len(values)
    ordered: list[float] = []
    for i, value in enumerate(values):
        if math.isnan(value):
            continue
        insort(ordered, value)
        if i >= window:
            old = values[i - window]
            if not math.isnan(old):
                del ordered[bisect_left(ordered, old)]
        if len(ordered) >= window:
            out[i] = bisect_right(ordered, value) / len(ordered)
    return out


def bar_ms(bars: Sequence[Bar]) -> int:
    """The bar spacing: the smallest gap between consecutive bars."""

    if len(bars) < 2:
        return 60_000
    return min(b.open_time - a.open_time for a, b in zip(bars[:50], bars[1:51], strict=False))


def higher_timeframe_ema(bars: Sequence[Bar], htf_minutes: int, period: int) -> list[float]:
    """EMA of higher-timeframe closes, known at bar i only once its HTF bar has closed.

    The HTF bar [k*H, (k+1)*H) closes at the close of the last base bar inside it; from that
    bar on, the EMA includes it. Earlier base bars see only completed HTF bars (no lookahead).
    NaN until ``period`` HTF bars have completed.
    """

    step = bar_ms(bars)
    htf = htf_minutes * 60_000
    if htf < step:
        raise ValueError("higher timeframe must not be shorter than the bars")
    alpha = 2.0 / (period + 1)
    out = [NAN] * len(bars)
    value: float | None = None
    completed = 0
    for i, b in enumerate(bars):
        bucket_end = (b.open_time // htf + 1) * htf
        if b.open_time + step >= bucket_end:  # this bar closes the HTF bar
            value = b.close if value is None else alpha * b.close + (1 - alpha) * value
            completed += 1
        if value is not None and completed >= period:
            out[i] = value
    return out
