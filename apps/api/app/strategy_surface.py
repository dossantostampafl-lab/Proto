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
from datetime import UTC, datetime

from fastapi import APIRouter

from services.execution.catalog import PAPER_INSTRUMENTS
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
_counters = {"ticks": 0, "signals": 0, "journal_failures": 0}
_task: asyncio.Task[None] | None = None


def reset_strategy_state() -> None:
    global strategy
    strategy = EmaCrossStrategy()
    _agent_strategies.clear()
    for key in _counters:
        _counters[key] = 0


def _strategy_for(instrument_id: str) -> EmaCrossStrategy:
    if not settings.agent_roster_enabled:
        return strategy
    from .roster_surface import ema_params

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
        signal = _strategy_for(instrument_id).observe(instrument_id, mid, now)
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
        **_counters,
        **_BOUNDARY,
    }
