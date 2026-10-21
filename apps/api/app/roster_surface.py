"""Trader-agent roster surface: virtual R$200 agents, their books and eliminations.

The roster (``services.agents.roster``) is durable on the control-plane store
(control_state). Fills are folded into the agents' books from the OMS orders this API
already reads (the isolated executor's, or the in-process paper OMS's); the executor's wire
contract is unchanged, the intent -> agent mapping lives in this API's database. Capital is
virtual testnet/paper sizing budget, never money. Read-only route: GET /v1/agents/roster.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter

from services.agents.roster import (
    AGENT_STARTING_CAPITAL,
    B3_SLOTS,
    INITIAL_SLOTS,
    AgentEliminated,
    AgentRoster,
    Slot,
    SlotKind,
    TraderAgent,
    elimination_floor,
    size_multiplier,
)
from services.execution import TradableInstrument
from services.execution.constitution import REFERENCE_CAPITAL

from . import control_state
from .event_state import record_operational_event
from .executor_client import ExecutorRefused, ExecutorUnavailable
from .instrument_registry import active_instruments
from .market_quotes import live_quote
from .settings import settings

logger = logging.getLogger("proto.agents.roster")

router = APIRouter(prefix="/v1/agents", tags=["agents"])

_BOUNDARY = {"financial_connectivity": False, "real_money_execution": False}
_MAX_MARK_AGE = timedelta(seconds=30)
_SYNC_LIMIT = 200

_roster: AgentRoster | None = None


async def _journal(event_type: str, payload: dict[str, Any], key: str) -> None:
    await record_operational_event(
        event_type=event_type, source="agent-roster", payload=payload, idempotency_key=key
    )


def roster_slots() -> tuple[Slot, ...]:
    """The Binance slots, plus the B3 ones when B3 agents are on and the XP node is configured."""

    if settings.agent_b3_enabled and settings.xp_routing_enabled:
        return INITIAL_SLOTS + B3_SLOTS
    return INITIAL_SLOTS


def roster() -> AgentRoster:
    global _roster
    if _roster is None:
        _roster = AgentRoster(
            control_state.current().engine,
            recorder=_journal,
            slots=roster_slots(),
            lab_strategies=settings.agent_lab_strategies_enabled,
        )
    return _roster


async def ready() -> AgentRoster:
    """The loaded roster, with every slot seeded (idempotent)."""

    book = roster()
    await book.seed()
    return book


def reset_roster_state() -> None:
    global _roster
    _roster = None


def data_mode() -> str:
    return "TESTNET" if settings.oms_execution_backend == "executor" else "PAPER"


async def brl_per(quote_asset: str) -> Decimal | None:
    """BRL per unit of quote: the same public USDTBRL ask the canary cap uses. None: unknown."""

    if quote_asset == "BRL":
        return Decimal("1")
    if settings.live_market_source != "BINANCE":
        return None
    from .fx_rates import rates

    return await rates().brl_per(quote_asset)


def mark(instrument_id: str) -> Decimal | None:
    """Live mid, or None when missing or older than 30 s (never a guessed price)."""

    quote = live_quote(instrument_id, market_source=settings.live_market_source)
    if quote is None or datetime.now(UTC) - quote.observed_at > _MAX_MARK_AGE:
        return None
    return (quote.bid + quote.ask) / 2


@dataclass(frozen=True)
class Valuation:
    equity_brl: Decimal | None
    brl_per_quote: Decimal | None
    mark: Decimal | None

    @property
    def known(self) -> bool:
        return self.equity_brl is not None and self.brl_per_quote is not None


async def value(agent: TraderAgent, *, for_trading: bool = False) -> Valuation:
    """Equity in BRL. The FX rate is read only when needed: to trade, or to value a book."""

    instrument = active_instruments().get(agent.slot.instrument_id)
    needs_fx = instrument is not None and (for_trading or not agent.book.empty)
    fx = await brl_per(instrument.quote_asset) if needs_fx and instrument is not None else None
    mid = mark(agent.slot.instrument_id)
    return Valuation(agent.equity_brl(mid, fx), fx, mid)


async def survive(agent: TraderAgent, valuation: Valuation, instrument: TradableInstrument) -> bool:
    """Eliminate the agent when its equity is below the smallest order it could place.

    An unknown valuation never eliminates (nor trades): the agent abstains instead.
    """

    if not agent.active:
        return False
    if valuation.equity_brl is None or valuation.brl_per_quote is None:
        return True
    floor = elimination_floor(instrument.min_notional, valuation.brl_per_quote)
    if valuation.equity_brl >= floor:
        return True
    reason = (
        f"equity R${valuation.equity_brl.quantize(Decimal('0.01'))} below the minimum order "
        f"R${floor.quantize(Decimal('0.01'))}"
    )
    try:
        await roster().eliminate(agent.agent_id, reason=reason, equity_brl=valuation.equity_brl)
    except AgentEliminated:
        pass
    return False


async def record_order(order: dict[str, Any]) -> list[dict[str, Any]]:
    """Fold one OMS order into its agent's book, then check that agent's survival."""

    book = roster()
    agent_id = book.agent_for_intent(str(order.get("intent_id") or ""))
    if agent_id is None:
        return []
    agent = book.get(agent_id)
    instrument = active_instruments().get(agent.slot.instrument_id) if agent else None
    applied = await book.apply_order(order, instrument=instrument)
    if applied and agent is not None and agent.active and instrument is not None:
        await survive(agent, await value(agent), instrument)
    return applied


async def sync_fills() -> bool:
    """Read recent OMS orders and apply new fills. False when the orders cannot be read."""

    from .oms_surface import recent_orders

    try:
        orders = await recent_orders(_SYNC_LIMIT)
    except (ExecutorUnavailable, ExecutorRefused) as exc:
        logger.warning("agent fills not synced: %s", exc)
        return False
    for order in reversed(orders):  # oldest first, so books replay in order
        await record_order(order)
    return True


def ema_params(instrument_id: str) -> tuple[int, int] | None:
    """(fast, slow) of the ACTIVE EMA agent on ``instrument_id``, from the loaded roster."""

    if _roster is None:
        return None
    for agent in _roster.agents():
        if (
            agent.active
            and agent.slot.kind is SlotKind.EMA
            and agent.slot.instrument_id == instrument_id
            and "fast" in agent.params
        ):
            return int(agent.params["fast"]), int(agent.params["slow"])
    return None


def lab_params(instrument_id: str) -> dict[str, Any] | None:
    """Lab strategy params of the ACTIVE own-strategy agent on ``instrument_id``, if it has one."""

    if _roster is None:
        return None
    for agent in _roster.agents():
        if (
            agent.active
            and agent.slot.kind is SlotKind.EMA
            and agent.slot.instrument_id == instrument_id
            and "lab_strategy" in agent.params
        ):
            return dict(agent.params)
    return None


def _agent_view(agent: TraderAgent, valuation: Valuation) -> dict[str, Any]:
    pnl_quote = agent.book.pnl(valuation.mark)
    unrealized = agent.book.unrealized(valuation.mark)
    equity = valuation.equity_brl
    pnl_brl = equity - agent.capital_brl if equity is not None else None
    return {
        **agent.to_dict(),
        "equity_brl": str(equity) if equity is not None else None,
        "pnl_brl": str(pnl_brl) if pnl_brl is not None else None,
        "pnl_quote": str(pnl_quote) if pnl_quote is not None else None,
        "unrealized_pnl_quote": str(unrealized) if unrealized is not None else None,
        "mark": str(valuation.mark) if valuation.mark is not None else None,
        "brl_per_quote": str(valuation.brl_per_quote) if valuation.brl_per_quote else None,
        "valuation": "MARKED" if equity is not None else "UNAVAILABLE",
        "survival_multiplier": str(size_multiplier(equity)) if equity is not None else None,
    }


@router.get("/roster")
async def roster_view() -> dict[str, object]:
    book = await ready()
    synced = await sync_fills()
    agents = []
    equity_total: Decimal | None = Decimal("0")
    for agent in book.agents():
        valuation = await value(agent)
        agents.append(_agent_view(agent, valuation))
        if agent.active:
            if valuation.equity_brl is None or equity_total is None:
                equity_total = None
            else:
                equity_total += valuation.equity_brl
    active = [a for a in book.agents() if a.active]
    allocated = book.allocated()
    return {
        "agents": agents,
        "totals": {
            "agents": len(agents),
            "active": len(active),
            "eliminated": len(agents) - len(active),
            "allocated_capital_brl": str(allocated),
            "reference_capital_brl": str(REFERENCE_CAPITAL),
            "unallocated_capital_brl": str(REFERENCE_CAPITAL - allocated),
            "active_equity_brl": str(equity_total) if equity_total is not None else None,
            "starting_capital_brl": str(AGENT_STARTING_CAPITAL),
        },
        "enabled": settings.agent_roster_enabled,
        "fills_synced": synced,
        "capital": "VIRTUAL",
        "currency": "BRL",
        "data_mode": data_mode(),
        "note": "virtual testnet/paper capital per agent; no real money is allocated",
        **_BOUNDARY,
    }
