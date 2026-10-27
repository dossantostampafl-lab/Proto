"""Live adapter: runs a lab strategy on live mids and emits :class:`Signal`s only.

Same interface as ``services.signals.proto_strategy.EmaCrossStrategy.observe``. Mids are
aggregated into bars of the strategy's timeframe; at each bar close the strategy's plan is
recomputed over the kept history and its last bar decides:

* flat and ``entry`` -> a LONG signal (strength = the plan's strength);
* holding and ``exit`` or the time stop -> a SHORT signal (a spot SHORT only sells holdings).

While holding, every mid is checked against the protective stop and the take-profit, which emit a
SHORT at once. The adapter's notion of "holding" is its own (it assumes its LONG was acted on);
a SHORT for a position the agent never got is refused downstream (nothing to sell), so the
assumption can only cost an abstention, never an order.

Signals carry no order authority: fusion, the reviewer, the Risk Engine permit and the OMS still
decide. Live mids carry no volume, so strategies that need volume (Donchian's confirmation)
never enter live unless their bars are warmed with real klines.
"""

from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from services.execution import Signal, SignalDirection, SignalSource
from services.research.data import Bar
from services.research.strategies import build

LAB_STRATEGY_PREFIX = "proto-lab-"
_NAMESPACE = uuid5(NAMESPACE_URL, "proto:strategy:lab")


def lab_strategy_id(spec: dict[str, Any]) -> str:
    """Stable strategy id of a lab spec: ``proto-lab-<kind>-<8 hex of the spec>``."""

    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    digest = uuid5(_NAMESPACE, canonical).hex[:8]
    kind = spec["strategy"]
    if kind == "regime":
        kind = f"regime-{spec['inner']['strategy']}"
    return f"{LAB_STRATEGY_PREFIX}{kind.replace('_', '-')}-{digest}"


@dataclass
class _Book:
    bars: deque[Bar]
    bar_start: datetime | None = None
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    last: float | None = None
    holding: bool = False
    stop: float = 0.0
    target: float = 0.0
    held_bars: int = 0


@dataclass
class LabSignalStrategy:
    """One lab spec on one timeframe, tracking every instrument it is fed."""

    spec: dict[str, Any]
    timeframe_minutes: int
    history: int = 1500
    _strategy: Any = field(init=False, repr=False)
    _books: dict[str, _Book] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if self.timeframe_minutes <= 0 or self.history < 10:
            raise ValueError("timeframe_minutes and history must be positive")
        self._strategy = build(self.spec)

    @property
    def strategy_id(self) -> str:
        return lab_strategy_id(self.spec)

    @property
    def bar(self) -> timedelta:
        return timedelta(minutes=self.timeframe_minutes)

    def _book(self, instrument_id: str) -> _Book:
        return self._books.setdefault(instrument_id, _Book(bars=deque(maxlen=self.history)))

    def warm(self, instrument_id: str, bars: list[Bar]) -> None:
        """Seed history with closed klines (e.g. public market data) so signals start sooner.

        Only bars older than what the book already holds (and than the bar still forming) are
        taken, each open time once and in order, so a late or repeated warm-up never duplicates
        or reorders history.
        """

        book = self._book(instrument_id)
        cutoff = min(
            ([book.bars[0].open_time] if book.bars else [])
            + ([int(book.bar_start.timestamp() * 1000)] if book.bar_start else []),
            default=None,
        )
        older = {b.open_time: b for b in bars if cutoff is None or b.open_time < cutoff}
        merged = [older[t] for t in sorted(older)] + list(book.bars)
        book.bars = deque(merged[-self.history :], maxlen=self.history)

    def observe(self, instrument_id: str, mid: float, at: datetime) -> Signal | None:
        if not (math.isfinite(mid) and mid > 0):
            return None
        book = self._book(instrument_id)
        start = _floor(at, self.bar)
        signal = None
        if book.bar_start is None:
            book.bar_start, book.open, book.high, book.low = start, mid, mid, mid
        elif start > book.bar_start and book.last is not None:
            closed = Bar(
                int(book.bar_start.timestamp() * 1000),
                book.open,
                book.high,
                book.low,
                book.last,
                0.0,
            )
            book.bars.append(closed)
            if start - book.bar_start == self.bar:
                signal = self._on_close(instrument_id, book, at, mid)
            elif book.holding:
                # The feed skipped at least one bar: the bar that just closed is old news for an
                # entry, but a holding still runs its exit rule and bar limit (only exits come out
                # of _on_close while holding).
                signal = self._on_close(instrument_id, book, at, mid)
            # Flat after a gap: the old bar decides nothing.
            book.bar_start, book.open, book.high, book.low = start, mid, mid, mid
        book.high, book.low, book.last = max(book.high, mid), min(book.low, mid), mid
        if signal is None and book.holding and (mid <= book.stop or mid >= book.target):
            book.holding = False
            signal = self._signal(instrument_id, SignalDirection.SHORT, 1.0, at, at)
        return signal

    def _on_close(self, instrument_id: str, book: _Book, at: datetime, mid: float) -> Signal | None:
        bars = list(book.bars)
        plan = self._strategy.plan(bars)
        i = len(bars) - 1
        closed_at = book.bar_start + self.bar if book.bar_start else at
        if book.holding:
            book.held_bars += 1
            timed_out = plan.max_bars is not None and book.held_bars >= plan.max_bars
            if plan.exit[i] or timed_out:
                book.holding = False
                return self._signal(instrument_id, SignalDirection.SHORT, 1.0, closed_at, at)
            return None
        if plan.entry[i] > 0:
            book.holding, book.held_bars = True, 0
            book.stop = mid * (1 - plan.stop[i])
            book.target = mid * (1 + plan.target[i])
            return self._signal(
                instrument_id, SignalDirection.LONG, min(plan.entry[i], 1.0), closed_at, at
            )
        return None

    def _signal(
        self,
        instrument_id: str,
        direction: SignalDirection,
        strength: float,
        observed_at: datetime,
        at: datetime,
    ) -> Signal:
        observed_at = min(observed_at, at)
        return Signal(
            signal_id=uuid5(
                _NAMESPACE,
                f"{self.strategy_id}\x00{instrument_id}\x00{observed_at.isoformat()}\x00{direction}",
            ),
            source=SignalSource.PROTO_STRATEGY,
            strategy_id=self.strategy_id,
            instrument_id=instrument_id,
            direction=direction,
            strength=round(strength, 4),
            timeframe=_label(self.timeframe_minutes),
            observed_at=observed_at,
            received_at=at,
        )

    def status(self) -> dict[str, object]:
        return {
            instrument: {"bars": len(book.bars), "holding": book.holding}
            for instrument, book in sorted(self._books.items())
        }


def _label(minutes: int) -> str:
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _floor(at: datetime, step: timedelta) -> datetime:
    seconds = int(step.total_seconds())
    epoch = int(at.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=at.tzinfo or UTC)
