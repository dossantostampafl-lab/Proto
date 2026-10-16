"""PROTO's own deterministic strategy: an EMA crossover on one-minute bars of the live mid.

The strategy only produces `Signal`s (opinions). It has no order authority: every signal still
goes through fusion, the shrink-only reviewer, the Risk Engine permit and the OMS. It is fed
public read-only quotes; when the feed is missing or stale it emits nothing.

A signal is emitted when the fast EMA crosses the slow EMA on a closed bar and the gap between
them is wide enough. Strength grows with the gap, so a weak cross stays below the fusion
threshold. A cooldown keeps one instrument from flapping.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from services.execution import Signal, SignalDirection, SignalSource

STRATEGY_ID = "proto-ema-cross-1m"
_NAMESPACE = uuid5(NAMESPACE_URL, "proto:strategy:ema-cross")


@dataclass(frozen=True)
class EmaCrossPolicy:
    bar: timedelta = timedelta(minutes=1)
    fast: int = 5
    slow: int = 20
    # Gap between the EMAs, as a fraction of price, that gives full strength.
    full_strength_gap: float = 0.002
    cooldown: timedelta = timedelta(minutes=15)

    def __post_init__(self) -> None:
        if not 1 < self.fast < self.slow:
            raise ValueError("need 1 < fast < slow")
        if self.full_strength_gap <= 0 or self.bar <= timedelta(0):
            raise ValueError("full_strength_gap and bar must be positive")


def _ema(values: list[float], period: int) -> list[float]:
    alpha = 2.0 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


@dataclass
class _Book:
    closes: deque[float]
    bar_start: datetime | None = None
    last_mid: float | None = None
    last_signal_at: datetime | None = None


@dataclass
class EmaCrossStrategy:
    """One instance tracks every instrument it is fed. Pure: time comes from the caller."""

    policy: EmaCrossPolicy = field(default_factory=EmaCrossPolicy)
    _books: dict[str, _Book] = field(default_factory=dict)

    def observe(self, instrument_id: str, mid: float, at: datetime) -> Signal | None:
        """Feed one live mid. Returns a signal when a closed bar completes a strong cross."""

        if not mid > 0:
            return None
        book = self._books.setdefault(
            instrument_id, _Book(closes=deque(maxlen=self.policy.slow * 3))
        )
        start = _floor(at, self.policy.bar)
        signal = None
        if book.bar_start is None:
            book.bar_start = start
        elif start > book.bar_start and book.last_mid is not None:
            # The previous bar closed at its last mid.
            book.closes.append(book.last_mid)
            book.bar_start = start
            signal = self._evaluate(instrument_id, book, at)
        book.last_mid = mid
        return signal

    def _evaluate(self, instrument_id: str, book: _Book, at: datetime) -> Signal | None:
        closes = list(book.closes)
        if len(closes) < self.policy.slow + 1:
            return None
        fast, slow = _ema(closes, self.policy.fast), _ema(closes, self.policy.slow)
        before, now = fast[-2] - slow[-2], fast[-1] - slow[-1]
        if before <= 0 < now:
            direction = SignalDirection.LONG
        elif before >= 0 > now:
            direction = SignalDirection.SHORT
        else:
            return None
        if book.last_signal_at is not None and at - book.last_signal_at < self.policy.cooldown:
            return None
        gap = abs(now) / closes[-1]
        strength = round(min(1.0, gap / self.policy.full_strength_gap), 4)
        book.last_signal_at = at
        bar_close = book.bar_start
        assert bar_close is not None
        return Signal(
            signal_id=uuid5(
                _NAMESPACE, f"{instrument_id}\x00{bar_close.isoformat()}\x00{direction}"
            ),
            source=SignalSource.PROTO_STRATEGY,
            strategy_id=STRATEGY_ID,
            instrument_id=instrument_id,
            direction=direction,
            strength=strength,
            timeframe="1m",
            observed_at=bar_close,
            received_at=max(at, bar_close),
        )

    def status(self) -> dict[str, object]:
        return {
            instrument: {
                "bars": len(book.closes),
                "needs_bars": self.policy.slow + 1,
                "last_signal_at": book.last_signal_at.isoformat() if book.last_signal_at else None,
            }
            for instrument, book in sorted(self._books.items())
        }


def _floor(at: datetime, step: timedelta) -> datetime:
    seconds = int(step.total_seconds())
    epoch = int(at.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=at.tzinfo)
