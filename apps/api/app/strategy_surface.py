"""PROTO's own strategy, fed by the live public market stream (signals only, no order authority).

Every few seconds the fresh Binance mid of each symbol is fed to the EMA-cross strategy. A
signal it produces is journaled as PROTO_STRATEGY_SIGNAL and handed to the agent pipeline,
exactly like an accepted TradingView alert. A missing or stale quote feeds nothing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from services.execution.catalog import PAPER_INSTRUMENTS
from services.research.live import LabSignalStrategy
from services.signals.proto_strategy import STRATEGY_ID, EmaCrossPolicy, EmaCrossStrategy

from .event_state import JournalUnavailable
from .market_quotes import live_quote
from .settings import settings
from .signal_surface import accept_internal_signal

logger = logging.getLogger("proto.strategy")

_POLL_SECONDS = 5.0
_MAX_QUOTE_AGE_SECONDS = 10.0
_INSTRUMENTS = tuple(
    i for i in sorted(PAPER_INSTRUMENTS) if i.startswith("BINANCE_SPOT:") and i.endswith("USDT")
)
_BOUNDARY = {
    "order_authority": False,
    "financial_connectivity": False,
    "real_money_execution": False,
}

strategy = EmaCrossStrategy()
# With the agent roster on, an EMA agent whose (fast, slow) differs from the default gets its
# own strategy instance per instrument (a replacement trades varied parameters).
_agent_strategies: dict[tuple[str, int, int], EmaCrossStrategy] = {}
# With agent_lab_strategies_enabled, an agent spawned with a lab strategy gets the lab adapter
# (signals only), one per lab strategy id; warmed once per instrument when enabled.
_lab_strategies: dict[str, LabSignalStrategy] = {}
_lab_warmed: set[tuple[str, str]] = set()
_counters = {"ticks": 0, "signals": 0, "journal_failures": 0}
_task: asyncio.Task[None] | None = None


def reset_strategy_state() -> None:
    global strategy
    strategy = EmaCrossStrategy()
    _agent_strategies.clear()
    _lab_strategies.clear()
    _lab_warmed.clear()
    for key in _counters:
        _counters[key] = 0


def _strategy_for(instrument_id: str) -> EmaCrossStrategy | LabSignalStrategy:
    if not settings.agent_roster_enabled:
        return strategy
    from .roster_surface import ema_params, lab_params

    if settings.agent_lab_strategies_enabled and (lab := lab_params(instrument_id)) is not None:
        found_lab = _lab_strategies.get(lab["lab_strategy"])
        if found_lab is None:
            found_lab = _lab_strategies[lab["lab_strategy"]] = LabSignalStrategy(
                lab["spec"], int(lab["timeframe_minutes"])
            )
        return found_lab

    params = ema_params(instrument_id)
    if params is None or params == (strategy.policy.fast, strategy.policy.slow):
        return strategy
    key = (instrument_id, *params)
    found = _agent_strategies.get(key)
    if found is None:
        fast, slow = params
        found = _agent_strategies[key] = EmaCrossStrategy(EmaCrossPolicy(fast=fast, slow=slow))
    return found


async def tick(now: datetime | None = None) -> int:
    """Feed one fresh mid per instrument; returns how many signals were accepted."""

    now = now or datetime.now(UTC)
    accepted = 0
    for instrument_id in _INSTRUMENTS:
        quote = live_quote(instrument_id, market_source=settings.live_market_source)
        if quote is None or (now - quote.observed_at).total_seconds() > _MAX_QUOTE_AGE_SECONDS:
            continue
        _counters["ticks"] += 1
        mid = float((quote.bid + quote.ask) / 2)
        chosen = _strategy_for(instrument_id)
        if isinstance(chosen, LabSignalStrategy):
            await _warm_lab(chosen, instrument_id)
        signal = chosen.observe(instrument_id, mid, now)
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


def _public_klines(instrument_id: str, minutes: int, bars: int) -> list:
    """Closed public klines (read-only market data) to seed a lab strategy's history."""

    from services.research.data import download

    interval = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h", 1440: "1d"}[minutes]
    end = datetime.now(UTC)
    return download(
        instrument_id.split(":", 1)[1], interval, end - bars * timedelta(minutes=minutes), end
    )


# Replaced in tests; never called unless agent_lab_warmup_klines is on.
lab_klines = _public_klines


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
        await asyncio.sleep(_POLL_SECONDS)


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
        "instruments": list(_INSTRUMENTS),
        "books": strategy.status(),
        "lab_strategies": {k: v.status() for k, v in sorted(_lab_strategies.items())},
        **_counters,
        **_BOUNDARY,
    }
