"""Strategy library of the lab: deterministic, long-only spot signal generators.

Every strategy turns closed bars into a :class:`Plan`, one entry per bar, all causal (bar i uses
bars 0..i only):

* ``entry[i]``  strength in (0, 1] to go LONG at the close of bar i (0: no entry);
* ``exit[i]``   True to leave a held position at the close of bar i (a spot SHORT is an exit);
* ``stop[i]``   protective stop distance, a fraction of the fill price below it;
* ``target[i]`` take-profit distance, a fraction above the fill price;
* ``max_bars``  time stop: bars a position may be held before it is closed.

So every entry always carries a protective stop and a take-profit. The backtester
(``services.research.backtest``) and the live adapter (``services.research.live``) execute the
same plan; in production the plan only ever becomes :class:`services.execution.Signal`s (LONG on
entry, SHORT on any exit), which still go through fusion, the reviewer, the Risk Engine permit
and the OMS. Nothing here has order authority.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from services.research import indicators as ind
from services.research.data import Bar


@dataclass
class Plan:
    entry: list[float]
    exit: list[bool]
    stop: list[float]
    target: list[float]
    max_bars: int | None = None

    @classmethod
    def empty(cls, n: int, *, stop: float = 0.02, target: float = 0.04) -> Plan:
        return cls([0.0] * n, [False] * n, [stop] * n, [target] * n)


class Strategy(Protocol):
    name: str

    def plan(self, bars: Sequence[Bar]) -> Plan: ...

    def describe(self) -> dict[str, Any]: ...


def _ok(*values: float) -> bool:
    return not any(math.isnan(v) for v in values)


def _atr_fraction(atr: float, close: float, mult: float, floor: float, cap: float) -> float:
    if not _ok(atr) or close <= 0:
        return floor
    return min(max(mult * atr / close, floor), cap)


@dataclass(frozen=True)
class _Base:
    def describe(self) -> dict[str, Any]:
        return {"strategy": self.name, **asdict(self)}  # type: ignore[attr-defined]


# (a) ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EmaCross(_Base):
    """Current production rule (services.signals.proto_strategy), on closed bars.

    A fast/slow EMA cross arms the instrument; the signal fires on the first closed bar within
    ``confirm_bars`` where the EMA gap reaches the entry strength (LONG) or exit strength
    (SHORT); a cooldown suppresses flapping. The lab adds the sizing stop (2%, the pipeline's
    ``agent_stop_distance``) and a take-profit so the agent always has a protective exit.
    """

    fast: int = 5
    slow: int = 20
    full_strength_gap: float = 0.001
    confirm_bars: int = 10
    entry_strength: float = 0.6
    exit_strength: float = 0.2
    cooldown_bars: int = 15
    stop_frac: float = 0.02
    target_frac: float = 0.04
    name: str = field(default="ema_cross", init=False)

    def plan(self, bars: Sequence[Bar]) -> Plan:
        n = len(bars)
        p = Plan.empty(n, stop=self.stop_frac, target=self.target_frac)
        closes = [b.close for b in bars]
        fast, slow = ind.ema(closes, self.fast), ind.ema(closes, self.slow)
        armed: tuple[int, int] | None = None  # (direction +1/-1, bars left)
        last_signal = -(10**9)
        for i in range(self.slow, n):
            before, now = fast[i - 1] - slow[i - 1], fast[i] - slow[i]
            if before <= 0 < now:
                armed = (1, self.confirm_bars)
            elif before >= 0 > now:
                armed = (-1, self.confirm_bars)
            if armed is None:
                continue
            direction, left = armed
            armed = (direction, left - 1) if left > 1 else None
            if (now > 0) != (direction > 0):
                armed = None
                continue
            strength = round(min(1.0, abs(now) / closes[i] / self.full_strength_gap), 4)
            needed = self.entry_strength if direction > 0 else self.exit_strength
            if strength < needed:
                continue
            armed = None
            if i - last_signal < self.cooldown_bars:
                continue
            last_signal = i
            if direction > 0:
                p.entry[i] = strength
            else:
                p.exit[i] = True
        return p


# (b) ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EmaTrend(_Base):
    """EMA cross filtered by a higher-timeframe trend, with ATR stop and target.

    Entry: fast EMA crosses above slow EMA while the close is above the HTF EMA (e.g. 1h EMA200).
    Exit: fast crosses back below slow, or the close falls below the HTF EMA.
    """

    fast: int = 12
    slow: int = 26
    htf_minutes: int = 60
    htf_period: int = 200
    atr_period: int = 14
    stop_atr: float = 2.0
    target_atr: float = 4.0
    name: str = field(default="ema_trend", init=False)

    def plan(self, bars: Sequence[Bar]) -> Plan:
        n = len(bars)
        p = Plan.empty(n)
        closes = [b.close for b in bars]
        fast, slow = ind.ema(closes, self.fast), ind.ema(closes, self.slow)
        trend = ind.higher_timeframe_ema(bars, self.htf_minutes, self.htf_period)
        atr = ind.atr(bars, self.atr_period)
        for i in range(max(self.slow, self.atr_period), n):
            c = closes[i]
            p.stop[i] = _atr_fraction(atr[i], c, self.stop_atr, 0.002, 0.2)
            p.target[i] = _atr_fraction(atr[i], c, self.target_atr, 0.004, 0.5)
            crossed_up = fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]
            crossed_down = fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]
            up = _ok(trend[i]) and c > trend[i]
            if crossed_up and up and _ok(atr[i]):
                p.entry[i] = 1.0
            if crossed_down or (_ok(trend[i]) and c < trend[i]):
                p.exit[i] = True
        return p


# (c) ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Donchian(_Base):
    """Breakout: close above the prior ``entry_period`` high on above-average volume.

    Exit on a close below the prior ``exit_period`` low. Stop and target in ATRs. Needs bar
    volume: live mids carry none, so the live adapter never enters on it without volume.
    """

    entry_period: int = 20
    exit_period: int = 10
    volume_period: int = 20
    volume_mult: float = 1.5
    atr_period: int = 14
    stop_atr: float = 2.0
    target_atr: float = 6.0
    name: str = field(default="donchian", init=False)

    def plan(self, bars: Sequence[Bar]) -> Plan:
        n = len(bars)
        p = Plan.empty(n)
        closes = [b.close for b in bars]
        upper = ind.prior_max([b.high for b in bars], self.entry_period)
        lower = ind.prior_min([b.low for b in bars], self.exit_period)
        volumes = [b.volume for b in bars]
        avg_volume = ind.sma(volumes, self.volume_period)
        atr = ind.atr(bars, self.atr_period)
        for i in range(1, n):
            c = closes[i]
            p.stop[i] = _atr_fraction(atr[i], c, self.stop_atr, 0.002, 0.2)
            p.target[i] = _atr_fraction(atr[i], c, self.target_atr, 0.004, 0.5)
            vol_ok = (
                _ok(avg_volume[i - 1])
                and avg_volume[i - 1] > 0
                and volumes[i] > self.volume_mult * avg_volume[i - 1]
            )
            if _ok(upper[i], atr[i]) and c > upper[i] and vol_ok:
                p.entry[i] = 1.0
            if _ok(lower[i]) and c < lower[i]:
                p.exit[i] = True
        return p


# (d) ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnorsRsi(_Base):
    """Connors-style mean reversion, only in an uptrend.

    Entry: close above its SMA(trend_period) and either RSI(2) below ``rsi_entry`` or the close
    below the lower Bollinger band. Exit: close back above SMA(exit_period), or the time stop.
    """

    trend_period: int = 200
    rsi_period: int = 2
    rsi_entry: float = 10.0
    bb_period: int = 20
    bb_k: float = 2.0
    exit_period: int = 5
    atr_period: int = 14
    stop_atr: float = 3.0
    target_atr: float = 3.0
    max_hold: int = 10
    name: str = field(default="connors_rsi", init=False)

    def plan(self, bars: Sequence[Bar]) -> Plan:
        n = len(bars)
        p = Plan.empty(n)
        p.max_bars = self.max_hold
        closes = [b.close for b in bars]
        trend = ind.sma(closes, self.trend_period)
        rsi = ind.rsi(closes, self.rsi_period)
        mid = ind.sma(closes, self.bb_period)
        std = ind.rolling_std(closes, self.bb_period)
        exit_ma = ind.sma(closes, self.exit_period)
        atr = ind.atr(bars, self.atr_period)
        for i in range(n):
            c = closes[i]
            p.stop[i] = _atr_fraction(atr[i], c, self.stop_atr, 0.002, 0.2)
            p.target[i] = _atr_fraction(atr[i], c, self.target_atr, 0.004, 0.5)
            if not _ok(trend[i], rsi[i], mid[i], std[i], atr[i]):
                continue
            oversold = rsi[i] < self.rsi_entry or c < mid[i] - self.bb_k * std[i]
            if c > trend[i] and oversold:
                p.entry[i] = 1.0
            if _ok(exit_ma[i]) and c > exit_ma[i]:
                p.exit[i] = True
        return p


# (e) ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TsMomentum(_Base):
    """Time-series momentum with volatility targeting.

    Long while the return over ``lookback`` bars is positive (and, when ``confirm_lookback`` is
    set, the longer-horizon return too); exit when it turns non-positive. Strength scales with
    ``target_vol / realized_vol`` (annualized, capped at 1), so size shrinks when volatility
    rises. ATR stop and target protect every position.
    """

    lookback: int = 24
    confirm_lookback: int = 0
    vol_period: int = 72
    target_vol: float = 0.5
    bars_per_year: float = 24 * 365
    atr_period: int = 14
    stop_atr: float = 3.0
    target_atr: float = 6.0
    name: str = field(default="ts_momentum", init=False)

    def plan(self, bars: Sequence[Bar]) -> Plan:
        n = len(bars)
        p = Plan.empty(n)
        closes = [b.close for b in bars]
        rets = [0.0] + [math.log(closes[i] / closes[i - 1]) for i in range(1, n)]
        vol = ind.rolling_std(rets, self.vol_period)
        atr = ind.atr(bars, self.atr_period)
        start = max(self.lookback, self.confirm_lookback, self.vol_period)
        for i in range(start, n):
            c = closes[i]
            p.stop[i] = _atr_fraction(atr[i], c, self.stop_atr, 0.002, 0.2)
            p.target[i] = _atr_fraction(atr[i], c, self.target_atr, 0.004, 0.5)
            mom = c / closes[i - self.lookback] - 1
            longer = c / closes[i - self.confirm_lookback] - 1 if self.confirm_lookback else 1.0
            if mom > 0 and longer > 0 and _ok(vol[i], atr[i]):
                annual = vol[i] * math.sqrt(self.bars_per_year)
                strength = 1.0 if annual <= 0 else min(1.0, self.target_vol / annual)
                p.entry[i] = round(max(strength, 0.01), 4)
            if mom <= 0:
                p.exit[i] = True
        return p


# (f) ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RegimeFilter:
    """Wraps any strategy: entries only while ATR% sits in a percentile band of its history.

    Exits, stops and targets of the inner strategy are untouched (a filter never blocks an exit).
    """

    inner: Any
    atr_period: int = 14
    window: int = 500
    low: float = 0.2
    high: float = 0.8

    @property
    def name(self) -> str:
        return f"regime({self.inner.name})"

    def describe(self) -> dict[str, Any]:
        return {
            "strategy": "regime",
            "inner": self.inner.describe(),
            "atr_period": self.atr_period,
            "window": self.window,
            "low": self.low,
            "high": self.high,
        }

    def plan(self, bars: Sequence[Bar]) -> Plan:
        p = self.inner.plan(bars)
        atr = ind.atr(bars, self.atr_period)
        atr_pct = [
            a / b.close if _ok(a) and b.close > 0 else math.nan
            for a, b in zip(atr, bars, strict=True)
        ]
        rank = ind.percentile_rank(atr_pct, self.window)
        for i, r in enumerate(rank):
            if p.entry[i] and not (_ok(r) and self.low <= r <= self.high):
                p.entry[i] = 0.0
        return p


@dataclass(frozen=True)
class RandomEntry(_Base):
    """Baseline: enters with probability ``probability`` per bar, deterministic per seed.

    Exits only through the time stop (``hold`` bars) or the protective stop/target.
    """

    probability: float = 0.02
    hold: int = 12
    seed: int = 0
    stop_frac: float = 0.02
    target_frac: float = 0.04
    name: str = field(default="random", init=False)

    def plan(self, bars: Sequence[Bar]) -> Plan:
        import random

        rng = random.Random(self.seed)
        p = Plan.empty(len(bars), stop=self.stop_frac, target=self.target_frac)
        p.max_bars = self.hold
        for i in range(len(bars)):
            if rng.random() < self.probability:
                p.entry[i] = 1.0
        return p


STRATEGIES: dict[str, type] = {
    "ema_cross": EmaCross,
    "ema_trend": EmaTrend,
    "donchian": Donchian,
    "connors_rsi": ConnorsRsi,
    "ts_momentum": TsMomentum,
}


def build(spec: dict[str, Any]) -> Any:
    """Rebuild a strategy from ``describe()`` output (or a hand-written spec)."""

    spec = dict(spec)
    kind = spec.pop("strategy")
    spec.pop("name", None)
    if kind == "regime":
        inner = build(spec.pop("inner"))
        return RegimeFilter(inner, **spec)
    if kind == "random":
        return RandomEntry(**spec)
    if kind not in STRATEGIES:
        raise ValueError(f"unknown lab strategy {kind!r}")
    return STRATEGIES[kind](**spec)
