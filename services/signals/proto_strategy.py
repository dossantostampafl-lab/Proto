"""PROTO's own deterministic strategy: an EMA crossover on bars of the live mid (1 to 10 minutes).

The strategy only produces `Signal`s (opinions). It has no order authority: every signal still
goes through fusion, the shrink-only reviewer, the Risk Engine permit and the OMS. It is fed
public read-only quotes; when the feed is missing or stale it emits nothing.

A cross of the fast EMA over the slow EMA on a closed bar arms the instrument. The signal is
emitted on the first closed bar, within `confirm_bars`, where the gap has opened in the cross's
direction enough to reach the emit strength (entries need more than exits). At the cross bar
itself the gap is close to zero, so emitting there gave signals too weak to ever trade. A cross
that never opens up, or that reverses first, emits nothing. A cooldown keeps one instrument from
flapping.
"""

from __future__ import annotations

import math
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
    full_strength_gap: float = 0.001
    # Closed bars after a cross during which the gap may open up to the emit strength.
    confirm_bars: int = 10
    # Strength needed to emit: an entry (LONG) asks for more than an exit (SHORT of holdings).
    entry_strength: float = 0.6
    exit_strength: float = 0.2
    cooldown: timedelta = timedelta(minutes=15)

    def __post_init__(self) -> None:
        if not 1 < self.fast < self.slow:
            raise ValueError("need 1 < fast < slow")
        if self.full_strength_gap <= 0 or self.bar <= timedelta(0) or self.confirm_bars < 1:
            raise ValueError("full_strength_gap, bar and confirm_bars must be positive")
        if not 0 < self.exit_strength <= self.entry_strength <= 1:
            raise ValueError("need 0 < exit_strength <= entry_strength <= 1")


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
    # (direction, closed bars left) after a cross that has not opened up yet.
    armed: tuple[SignalDirection, int] | None = None


@dataclass
class EmaCrossStrategy:
    """One instance tracks every instrument it is fed. Pure: time comes from the caller."""

    policy: EmaCrossPolicy = field(default_factory=EmaCrossPolicy)
    _books: dict[str, _Book] = field(default_factory=dict)

    def observe(self, instrument_id: str, mid: float, at: datetime) -> Signal | None:
        """Feed one live mid. Returns a signal when a closed bar completes a strong cross."""

        if not (math.isfinite(mid) and mid > 0):
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
            contiguous = start - book.bar_start == self.policy.bar
            book.closes.append(book.last_mid)
            book.bar_start = start
            if contiguous:
                signal = self._evaluate(instrument_id, book, at)
            else:
                # The feed skipped at least one bar (outage, stale quotes, a closed session):
                # a pending cross is not confirmed across the gap and no entry (LONG) comes out of
                # the old bar, but a bearish cross still exits (SHORT only reduces exposure).
                book.armed = None
                exit_signal = self._evaluate(instrument_id, book, at)
                book.armed = None
                if exit_signal is not None and exit_signal.direction is SignalDirection.SHORT:
                    signal = exit_signal
        book.last_mid = mid
        return signal

    def warm(self, instrument_id: str, closes: list[float]) -> None:
        """Seed an instrument that has no closed bar yet with closes of already closed bars.

        The bars must end before the bar now forming (e.g. public klines up to the last closed
        bar); live mids then continue from there. An instrument with live history is left alone.
        """

        book = self._books.setdefault(
            instrument_id, _Book(closes=deque(maxlen=self.policy.slow * 3))
        )
        if book.closes:
            return
        book.closes.extend(c for c in closes if math.isfinite(c) and c > 0)

    def _evaluate(self, instrument_id: str, book: _Book, at: datetime) -> Signal | None:
        closes = list(book.closes)
        if len(closes) < self.policy.slow + 1:
            return None
        fast, slow = _ema(closes, self.policy.fast), _ema(closes, self.policy.slow)
        before, now = fast[-2] - slow[-2], fast[-1] - slow[-1]
        if before <= 0 < now:
            book.armed = (SignalDirection.LONG, self.policy.confirm_bars)
        elif before >= 0 > now:
            book.armed = (SignalDirection.SHORT, self.policy.confirm_bars)
        if book.armed is None:
            return None
        direction, bars_left = book.armed
        book.armed = (direction, bars_left - 1) if bars_left > 1 else None
        if (now > 0) != (direction is SignalDirection.LONG):
            book.armed = None  # reversed before it opened up
            return None
        gap = abs(now) / closes[-1]
        strength = round(min(1.0, gap / self.policy.full_strength_gap), 4)
        needed = (
            self.policy.entry_strength
            if direction is SignalDirection.LONG
            else self.policy.exit_strength
        )
        if strength < needed:
            return None
        book.armed = None
        if book.last_signal_at is not None and at - book.last_signal_at < self.policy.cooldown:
            return None
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
            timeframe=_timeframe(self.policy.bar),
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


def _timeframe(bar: timedelta) -> str:
    minutes, seconds = divmod(int(bar.total_seconds()), 60)
    return f"{minutes}m" if not seconds else f"{int(bar.total_seconds())}s"


def _floor(at: datetime, step: timedelta) -> datetime:
    seconds = int(step.total_seconds())
    epoch = int(at.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=at.tzinfo)
