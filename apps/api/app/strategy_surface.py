"""PROTO's own strategy, fed by the live public market stream (signals only, no order authority).

Every few seconds (every second for bars under a minute) the fresh Binance mid of each symbol is
fed to the EMA-cross strategy (bars of 5 seconds to 10 minutes). A signal it produces is journaled
as PROTO_STRATEGY_SIGNAL and handed to the agent pipeline, exactly like an accepted TradingView
alert. A missing or stale quote feeds nothing.

With ``agent_ema_warmup_klines`` the EMA bars are seeded from Binance public klines at start (no
hours of idle warm-up after a restart), and ``agent_ema_trend_filter`` lets an entry (LONG) through
only while the mid is above the 1h EMA200 of those klines; an unknown trend blocks entries, never
exits. The same mid checks the EMA agent's protective stop and target: at ``agent_stop_loss``
under (or ``agent_take_profit`` above) the agent's average cost a SHORT of strength 1.0 goes the
same way (journaled, then fusion, Risk Engine, OMS).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter

from services.execution import Signal, SignalDirection, SignalSource
from services.execution.catalog import PAPER_INSTRUMENTS
from services.research.live import LabSignalStrategy
from services.signals.proto_strategy import STRATEGY_ID, EmaCrossPolicy, EmaCrossStrategy

from .event_state import JournalUnavailable
from .market_quotes import live_quote
from .settings import settings
from .signal_surface import accept_internal_signal

logger = logging.getLogger("proto.strategy")

_POLL_SECONDS = 5.0
# Bars under a minute need a mid every second (the public ticker stream updates once a second).
_FAST_POLL_SECONDS = 1.0
_MAX_QUOTE_AGE_SECONDS = 10.0
# A stop that was not acted on (refused, nothing filled) is sent again at most this often.
_STOP_RETRY = timedelta(seconds=60)
_STOP_NAMESPACE = uuid5(NAMESPACE_URL, "proto:strategy:protective-stop")
# Hourly trend for the entry filter: EMA200 of 1h closes, refreshed every hour; a failed read is
# retried after a few minutes and blocks entries meanwhile.
_TREND_MINUTES, _TREND_PERIOD, _TREND_BARS = 60, 200, 600
_TREND_REFRESH = timedelta(hours=1)
_TREND_RETRY = timedelta(minutes=5)
_INSTRUMENTS = tuple(
    i for i in sorted(PAPER_INSTRUMENTS) if i.startswith("BINANCE_SPOT:") and i.endswith("USDT")
)


def instruments() -> tuple[str, ...]:
    """Binance pairs, plus the B3 (US) agents' symbols while B3 (US) agents and XP (Alpaca)
    routing are on.

    B3 quotes come from the MT5 terminal with its own timestamps: outside the session they go
    stale and feed nothing, and the Risk Engine refuses B3 orders outside 10:00-17:55 anyway.
    """

    found = _INSTRUMENTS
    if settings.agent_more_pairs:
        from services.execution.catalog import BINANCE_MORE_INSTRUMENTS

        found += tuple(sorted(BINANCE_MORE_INSTRUMENTS))
    if settings.agent_b3_enabled and settings.xp_routing_enabled:
        from services.agents.roster import B3_SLOTS

        found += tuple(slot.instrument_id for slot in B3_SLOTS)
    if settings.agent_us_enabled and settings.alpaca_routing_enabled:
        # Alpaca IEX quotes carry Alpaca's timestamps: stale outside the session, and the Risk
        # Engine refuses US orders outside 09:30-16:00 New York anyway.
        from services.agents.roster import US_SLOTS

        found += tuple(slot.instrument_id for slot in US_SLOTS)
    return found


_BOUNDARY = {
    "order_authority": False,
    "financial_connectivity": False,
    "real_money_execution": False,
}

strategy = EmaCrossStrategy()
# With the agent roster on, an EMA agent whose (fast, slow) differs from the default gets its
# own strategy instance per instrument (a replacement trades varied parameters).
# Keyed by (instrument, fast, slow, bar seconds).
_agent_strategies: dict[tuple[str, int, int, int], EmaCrossStrategy] = {}
# Last protective stop sent per agent, so a stop is retried once a minute, not every tick.
_stops_sent: dict[str, datetime] = {}
_ema_warmed: set[tuple[str, int, int, int | str]] = set()
# instrument -> (read at, 1h EMA200 or None when the read failed)
_trend: dict[str, tuple[datetime, float | None]] = {}
# With agent_lab_strategies_enabled, an agent spawned with a lab strategy gets the lab adapter
# (signals only), one per lab strategy id; warmed once per instrument when enabled.
_lab_strategies: dict[str, LabSignalStrategy] = {}
_lab_warmed: set[tuple[str, str]] = set()
_counters = {"ticks": 0, "signals": 0, "journal_failures": 0, "trend_blocked": 0}
_task: asyncio.Task[None] | None = None


def reset_strategy_state() -> None:
    global strategy
    strategy = EmaCrossStrategy()
    _agent_strategies.clear()
    _stops_sent.clear()
    _ema_warmed.clear()
    _trend.clear()
    _lab_strategies.clear()
    _lab_warmed.clear()
    for key in _counters:
        _counters[key] = 0


def _strategy_for(instrument_id: str) -> EmaCrossStrategy | LabSignalStrategy:
    if not settings.agent_roster_enabled:
        return strategy
    from .roster_surface import ema_bar_seconds, ema_params, lab_params

    if settings.agent_lab_strategies_enabled and (lab := lab_params(instrument_id)) is not None:
        found_lab = _lab_strategies.get(lab["lab_strategy"])
        if found_lab is None:
            found_lab = _lab_strategies[lab["lab_strategy"]] = LabSignalStrategy(
                lab["spec"], int(lab["timeframe_minutes"])
            )
        return found_lab

    pair = (settings.agent_ema_fast, settings.agent_ema_slow)
    if pair == (0, 0):
        pair = ema_params(instrument_id) or (strategy.policy.fast, strategy.policy.slow)
    seconds = ema_bar_seconds(instrument_id)
    cooldown = settings.agent_ema_cooldown_minutes
    if (*pair, timedelta(seconds=seconds), timedelta(minutes=cooldown)) == (
        strategy.policy.fast,
        strategy.policy.slow,
        strategy.policy.bar,
        strategy.policy.cooldown,
    ):
        return strategy
    key = (instrument_id, *pair, seconds)
    found = _agent_strategies.get(key)
    if found is None:
        fast, slow = pair
        found = _agent_strategies[key] = EmaCrossStrategy(
            EmaCrossPolicy(
                bar=timedelta(seconds=seconds),
                fast=fast,
                slow=slow,
                cooldown=timedelta(minutes=cooldown),
            )
        )
    return found


async def tick(now: datetime | None = None) -> int:
    """Feed one fresh mid per instrument; returns how many signals were accepted."""

    now = now or datetime.now(UTC)
    accepted = 0
    for instrument_id in instruments():
        quote = live_quote(instrument_id, market_source=settings.live_market_source)
        if quote is None or (now - quote.observed_at).total_seconds() > _MAX_QUOTE_AGE_SECONDS:
            continue
        _counters["ticks"] += 1
        mid = float((quote.bid + quote.ask) / 2)
        chosen = _strategy_for(instrument_id)
        if isinstance(chosen, LabSignalStrategy):
            await _warm_lab(chosen, instrument_id)
        else:
            await _warm_ema(chosen, instrument_id)
        signal = chosen.observe(instrument_id, mid, now)
        if (
            signal is not None
            and isinstance(chosen, EmaCrossStrategy)
            and signal.direction is SignalDirection.LONG
            and not await _trend_allows_entry(instrument_id, mid, now)
        ):
            _counters["trend_blocked"] += 1
            signal = None
        if signal is None:
            signal = _protective_stop(instrument_id, quote.bid, now)
        if signal is None:
            continue
        try:
            if await accept_internal_signal(
                signal, event_type="PROTO_STRATEGY_SIGNAL", source="proto-strategy"
            ):
                _counters["signals"] += 1
                accepted += 1
        except JournalUnavailable:
            _counters["journal_failures"] += 1
            logger.exception("strategy signal %s was not journaled", signal.signal_id)
    return accepted


def _protective_stop(instrument_id: str, bid: Decimal, now: datetime) -> Signal | None:
    """A SHORT when the EMA agent's own position is ``agent_stop_loss`` under its cost, or
    ``agent_take_profit`` above it (timeframe ``stop`` or ``target``).

    Measured at the bid, the price a sale would get. Signals only: fusion, the Risk Engine permit
    and the OMS still decide, and a SHORT only ever sells the agent's own holdings.
    """

    if not settings.agent_roster_enabled:
        return None
    from .roster_surface import stopped_out

    for agent, reason in stopped_out(instrument_id, bid):
        last = _stops_sent.get(agent.agent_id)
        if last is not None and now - last < _STOP_RETRY:
            continue
        _stops_sent[agent.agent_id] = now
        minute = now.replace(second=0, microsecond=0)
        return Signal(
            signal_id=uuid5(
                _STOP_NAMESPACE, f"{agent.agent_id}\x00{instrument_id}\x00{minute.isoformat()}"
            ),
            source=SignalSource.PROTO_STRATEGY,
            strategy_id=STRATEGY_ID,
            instrument_id=instrument_id,
            direction=SignalDirection.SHORT,
            strength=1.0,
            timeframe=reason,
            observed_at=now,
            received_at=now,
        )
    return None


def _binance(instrument_id: str) -> bool:
    return instrument_id.startswith("BINANCE_SPOT:")


def _bar_label(seconds: int) -> str:
    return f"{seconds // 60}m" if seconds % 60 == 0 else f"{seconds}s"


async def _warm_ema(chosen: EmaCrossStrategy, instrument_id: str) -> None:
    """Seed the EMA bars once per (instrument, pair, bar) from Binance public klines: 1m klines
    for minute bars, 1s klines for bars under a minute."""

    policy = chosen.policy
    seconds = int(policy.bar.total_seconds())
    minute_bar = seconds % 60 == 0
    key = (instrument_id, policy.fast, policy.slow, seconds // 60 if minute_bar else f"{seconds}s")
    if not settings.agent_ema_warmup_klines or not _binance(instrument_id) or key in _ema_warmed:
        return
    _ema_warmed.add(key)
    wanted = policy.slow * 3
    unit = 60 if minute_bar else 1
    per_bar = seconds // unit
    try:
        count = wanted * per_bar + per_bar
        if minute_bar:
            raw = await asyncio.to_thread(ema_klines, instrument_id, 1, count)
        else:
            raw = await asyncio.to_thread(ema_second_klines, instrument_id, count)
    except Exception:
        logger.exception("EMA warm-up failed for %s; it warms from live mids", instrument_id)
        return
    from services.research.data import resample_ms

    step = seconds * 1000
    bars = raw if per_bar == 1 else resample_ms(raw, step)
    # Only whole bars that closed before the bar now forming (resample keeps a partial tail).
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    closed = [b.close for b in bars if b.open_time + step <= now_ms - now_ms % step]
    chosen.warm(instrument_id, closed[-wanted:])


async def _trend_allows_entry(instrument_id: str, mid: float, now: datetime) -> bool:
    """Whether an entry may go out: mid above the 1h EMA200 (Binance pairs).

    Active with the filter and the klines on (production). With the klines on, a trend that
    cannot be read blocks the entry.
    """

    if not (settings.agent_ema_trend_filter and settings.agent_ema_warmup_klines):
        return True
    if not _binance(instrument_id):
        return True
    read = _trend.get(instrument_id)
    wait = _TREND_REFRESH if read is not None and read[1] is not None else _TREND_RETRY
    stale = read is None or now - read[0] >= wait
    if stale:
        level: float | None
        try:
            bars = await asyncio.to_thread(ema_klines, instrument_id, _TREND_MINUTES, _TREND_BARS)
            closes = [b.close for b in bars]
            level = _ema_last(closes, _TREND_PERIOD) if len(closes) > _TREND_PERIOD else None
        except Exception:
            logger.exception("hourly trend unavailable for %s; entries blocked", instrument_id)
            level = None
        read = _trend[instrument_id] = (now, level)
    assert read is not None
    return read[1] is not None and mid > read[1]


def _ema_last(values: list[float], period: int) -> float:
    alpha = 2.0 / (period + 1)
    level = values[0]
    for value in values[1:]:
        level = alpha * value + (1 - alpha) * level
    return level


def _public_klines(instrument_id: str, minutes: int, bars: int) -> list:
    """Closed public klines (read-only market data) to seed a lab strategy's history."""

    from services.research.data import download

    interval = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h", 1440: "1d"}[minutes]
    end = datetime.now(UTC)
    return download(
        instrument_id.split(":", 1)[1], interval, end - bars * timedelta(minutes=minutes), end
    )


def _public_second_klines(instrument_id: str, bars: int) -> list:
    """Closed public 1s klines (read-only market data) to seed EMA bars under a minute."""

    from services.research.data import download

    end = datetime.now(UTC)
    return download(instrument_id.split(":", 1)[1], "1s", end - timedelta(seconds=bars), end)


# Replaced in tests; never called unless agent_lab_warmup_klines is on.
lab_klines = _public_klines
# Replaced in tests; never called unless agent_ema_warmup_klines is on.
ema_klines = _public_klines
ema_second_klines = _public_second_klines


async def _warm_lab(lab: LabSignalStrategy, instrument_id: str) -> None:
    key = (lab.strategy_id, instrument_id)
    if not settings.agent_lab_warmup_klines or key in _lab_warmed:
        return
    _lab_warmed.add(key)
    try:
        bars = await asyncio.to_thread(
            lab_klines, instrument_id, lab.timeframe_minutes, lab.history
        )
    except Exception:
        logger.exception("lab warm-up failed for %s; it warms from live mids", instrument_id)
        return
    lab.warm(instrument_id, bars)


async def _run() -> None:
    while True:
        try:
            await tick()
        except Exception:
            logger.exception("strategy tick failed")
        await asyncio.sleep(_poll_seconds())


def _poll_seconds() -> float:
    """Every second while EMA agents trade bars under a minute, else every few seconds."""

    if settings.agent_roster_enabled and 0 < settings.agent_ema_bar_seconds < 60:
        return _FAST_POLL_SECONDS
    return _POLL_SECONDS


@asynccontextmanager
async def strategy_lifespan(_: APIRouter) -> AsyncIterator[None]:
    global _task
    if settings.proto_strategy_enabled:
        _task = asyncio.get_running_loop().create_task(_run(), name="proto-strategy")
    try:
        yield
    finally:
        if _task is not None:
            _task.cancel()
            with suppress(asyncio.CancelledError):
                await _task
            _task = None


router = APIRouter(prefix="/v1/strategy", tags=["strategy"], lifespan=strategy_lifespan)


@router.get("/status")
def strategy_status() -> dict[str, object]:
    return {
        "enabled": settings.proto_strategy_enabled,
        "running": _task is not None and not _task.done(),
        "strategy_id": STRATEGY_ID,
        "market_source": settings.live_market_source,
        "instruments": list(instruments()),
        "books": strategy.status(),
        # The EMA agents' own strategies: per (instrument, pair, bar), bars held and last signal.
        "agent_strategies": {
            f"{instrument}|{fast}/{slow}|{_bar_label(seconds)}": found.status().get(instrument, {})
            for (instrument, fast, slow, seconds), found in sorted(_agent_strategies.items())
        },
        "bar": _bar_label(settings.agent_ema_bar_seconds or settings.agent_ema_bar_minutes * 60),
        "cooldown_minutes": settings.agent_ema_cooldown_minutes,
        "warmed_from_klines": sorted("|".join(map(str, key)) for key in _ema_warmed),
        # 1h EMA200 per instrument for the entry filter (null: the last read failed).
        "trend": {
            instrument: {"read_at": at.isoformat(), "ema200_1h": level}
            for instrument, (at, level) in sorted(_trend.items())
        },
        "lab_strategies": {k: v.status() for k, v in sorted(_lab_strategies.items())},
        **_counters,
        **_BOUNDARY,
    }
