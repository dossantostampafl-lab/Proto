from __future__ import annotations

import random
from collections.abc import Sequence


def delay_signal(signal: Sequence[float], periods: int, *, fill_value: float = 0.0) -> list[float]:
    """Delay a signal by an integer number of observations without lookahead.

    A value available at index i becomes actionable only at i + periods. Leading positions are
    filled explicitly so validation can measure sensitivity to realistic decision latency.
    """

    if periods < 0:
        raise ValueError("periods must be non-negative")
    values = [float(value) for value in signal]
    if periods == 0:
        return values
    if periods >= len(values):
        return [float(fill_value)] * len(values)
    return [float(fill_value)] * periods + values[:-periods]


def timestamp_shuffle(values: Sequence[float], *, seed: int) -> list[float]:
    """Destroy temporal ordering while preserving the empirical value distribution."""

    shuffled = [float(value) for value in values]
    random.Random(seed).shuffle(shuffled)
    return shuffled


def apply_signal_returns(signal: Sequence[float], returns: Sequence[float]) -> list[float]:
    """Apply aligned signal exposure to returns for deterministic negative-control tests."""

    if len(signal) != len(returns):
        raise ValueError("signal and returns must have equal length")
    return [float(position) * float(ret) for position, ret in zip(signal, returns, strict=True)]
