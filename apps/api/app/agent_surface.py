"""Hybrid agent pipeline surface (Phase 2). Decisions are proposals with no order authority.

Accepted signals are buffered here; when the pipeline is enabled each new
signal triggers an evaluation for its instrument. Every decision is written to
the operational journal as AGENT_DECISION. Equity and positions come from the
paper ledger (Phase 4); sizing never exceeds the constitutional reference
budget. Proposals go to the Risk Engine, and approved ones to the paper OMS
when paper execution is enabled.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections import Counter, deque
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.agents import (
    AgentDecision,
    AgentReview,
    ClaudeReviewer,
    DecisionOutcome,
    DeterministicReviewer,
    HybridDecisionPipeline,
    PortfolioView,
    ReviewContext,
    SizingPolicy,
)
from services.agents.fusion import DEFAULT_FUSION_POLICY, FusionPolicy
from services.agents.review import defer
from services.agents.roster import (
    AgentEliminated,
    Slot,
    TraderAgent,
    entry_min_score,
    shape_for_agent,
    size_multiplier,
    slot_of,
)
from services.execution import Signal, TradeIntent
from services.execution.constitution import REFERENCE_CAPITAL
from services.execution.venue import Quote
from services.risk import RiskDecision

from .event_state import JournalUnavailable, record_operational_event
from .instrument_registry import active_instruments
from .live_monitor import live_monitor  # noqa: F401  (re-exported for tests)
from .market_quotes import live_quote as _market_quote
from .oms_surface import account_state as ledger_account_state
from .oms_surface import execute as execute_paper
from .risk_surface import assess as assess_risk
from .settings import settings

logger = logging.getLogger("proto.agents")

router = APIRouter(prefix="/v1/agents", tags=["agents"])

_SIGNAL_CAPACITY = 500
_DECISION_CAPACITY = 100
_BOUNDARY = {
    "order_authority": False,
    "financial_connectivity": False,
    "real_money_execution": False,
}

_signals: deque[Signal] = deque(maxlen=_SIGNAL_CAPACITY)
_decisions: deque[AgentDecision] = deque(maxlen=_DECISION_CAPACITY)
_outcomes: Counter[str] = Counter()
_background: set[asyncio.Task[None]] = set()
_pipeline: HybridDecisionPipeline | None = None
_pipeline_settings_id: int | None = None
_record_failures = 0


class UnavailableReviewer:
    """Configured AI reviewer could not be built: defer everything, never approve."""

    name = "claude-unavailable"

    def __init__(self, reason: str) -> None:
        self._reason = reason

    async def review(self, context: ReviewContext) -> AgentReview:
        return defer(self.name, self._reason)


def _reviewer():
    if settings.agent_reviewer != "claude":
        return DeterministicReviewer()
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(max_retries=0, timeout=settings.agent_timeout_seconds)
    except Exception as exc:  # missing SDK or credentials
        logger.error("AI reviewer unavailable: %s", type(exc).__name__)
        return UnavailableReviewer(f"AI reviewer unavailable: {type(exc).__name__}")
    return ClaudeReviewer(
        client,
        model=settings.agent_model,
        effort=settings.agent_effort,
        timeout_seconds=settings.agent_timeout_seconds,
    )


def live_quote(instrument_id: str) -> Quote | None:
    return _market_quote(instrument_id, market_source=settings.live_market_source)


_portfolio_views: dict[str, PortfolioView] = {}


def _portfolio(instrument_id: str) -> PortfolioView:
    return _portfolio_views.get(
        instrument_id, PortfolioView(equity=REFERENCE_CAPITAL, position_quantity=Decimal("0"))
    )


async def _refresh_portfolio(instrument_id: str) -> None:
    """Size from the paper ledger: equity in the quote currency and the held position."""

    instrument = active_instruments().get(instrument_id)
    if instrument is None:
        return
    state = await ledger_account_state(instrument.quote_asset)
    # Sized on the account's own reference capital (R$ 10,000 in its quote currency), as the
    # Risk Engine checks it; an unknown one sizes nothing.
    _portfolio_views[instrument_id] = PortfolioView(
        equity=min(state.equity, state.reference_capital),
        position_quantity=state.positions.get(instrument_id, Decimal("0")),
    )


async def _record(decision: AgentDecision, *, agent: dict[str, object] | None = None) -> None:
    global _record_failures
    _decisions.append(decision)
    _outcomes[decision.outcome.value] += 1
    payload = decision.to_dict()
    if agent is not None:
        payload["agent_id"] = agent["agent_id"]
        payload["agent"] = agent
    try:
        await record_operational_event(
            event_type="AGENT_DECISION",
            source="agent-pipeline",
            payload=payload,
            idempotency_key=f"agent-decision:{decision.decision_id}",
        )
    except JournalUnavailable:
        _record_failures += 1
        logger.exception("agent decision %s was not journaled", decision.decision_id)


def pipeline() -> HybridDecisionPipeline:
    global _pipeline, _pipeline_settings_id
    if _pipeline is None or _pipeline_settings_id != id(settings):
        _pipeline = HybridDecisionPipeline(
            instruments=active_instruments(),
            reviewer=_reviewer(),
            quotes=live_quote,
            portfolio=_portfolio,
            recorder=_record,
            sizing_policy=SizingPolicy(stop_distance=Decimal(str(settings.agent_stop_distance))),
        )
        _pipeline_settings_id = id(settings)
    return _pipeline


def reset_agent_state() -> None:
    global _pipeline, _pipeline_settings_id, _record_failures
    for task in list(_background):
        task.cancel()
    _background.clear()
    _signals.clear()
    _decisions.clear()
    _outcomes.clear()
    _portfolio_views.clear()
    _slot_locks.clear()
    _pipeline = None
    _pipeline_settings_id = None
    _record_failures = 0


async def _evaluate(
    instrument_id: str,
) -> tuple[AgentDecision, RiskDecision | None, dict | None]:
    """Pipeline -> Risk Engine permit -> paper OMS (when paper execution is enabled)."""

    await _refresh_portfolio(instrument_id)
    decision = await pipeline().evaluate(instrument_id, list(_signals))
    if decision.proposal is None:
        return decision, None, None
    risk = await assess_risk(decision.proposal)
    order = None
    if risk.approved and risk.permit is not None:
        order = await execute_paper(decision.proposal, risk.permit)
    return decision, risk, order


async def submit_for_agent(
    agent_id: str, proposal: TradeIntent
) -> tuple[RiskDecision | None, dict | None]:
    """An agent's proposal -> Risk Engine -> OMS, attributed to that agent.

    An ELIMINATED (or unknown) agent code is dropped here, before the Risk Engine ever sees
    the intent. The intent -> agent mapping is written before dispatch, so a fill read back
    later (even after a restart) is attributed.
    """

    from . import roster_surface

    book = roster_surface.roster()
    await book.load()
    if not book.admit(agent_id):
        logger.warning("intent %s of non-active agent %s dropped", proposal.intent_id, agent_id)
        return None, None
    try:
        await book.attribute(
            str(proposal.intent_id),
            agent_id,
            instrument_id=proposal.instrument_id,
            side=proposal.side,
        )
    except AgentEliminated:
        logger.warning("intent %s of eliminated agent %s dropped", proposal.intent_id, agent_id)
        return None, None
    risk = await assess_risk(proposal)
    order = None
    if risk.approved and risk.permit is not None:
        order = await execute_paper(proposal, risk.permit)
    if order is not None:
        await roster_surface.record_order(order)
    return risk, order


_slot_locks: dict[str, asyncio.Lock] = {}


async def _evaluate_agent(
    slot: Slot,
) -> tuple[AgentDecision | None, RiskDecision | None, dict | None]:
    """The ACTIVE agent of ``slot`` decides on its own signals, sized on its own book.

    One evaluation per slot at a time, so two signals cannot both size on the same book.
    """

    lock = _slot_locks.setdefault(slot.key, asyncio.Lock())
    async with lock:
        return await _evaluate_agent_locked(slot)


async def _evaluate_agent_locked(
    slot: Slot,
) -> tuple[AgentDecision | None, RiskDecision | None, dict | None]:
    from . import roster_surface

    book = await roster_surface.ready()
    instrument = active_instruments().get(slot.instrument_id)
    # Only this slot's venue: a switched-off XP node must not slow a Binance agent down.
    await roster_surface.sync_fills([instrument.venue] if instrument is not None else None)
    agent: TraderAgent | None = book.active_for(slot)
    if agent is None or instrument is None:
        return None, None, None
    valuation = await roster_surface.value(agent, for_trading=True)
    if not valuation.known:
        logger.warning("agent %s abstains: no BRL rate or no live mark", agent.agent_id)
        return None, None, None
    if not await roster_surface.survive(agent, valuation, instrument):
        return None, None, None
    equity = valuation.equity_brl
    fx = valuation.brl_per_quote
    assert equity is not None and fx is not None

    await _refresh_portfolio(slot.instrument_id)
    min_score = entry_min_score(DEFAULT_FUSION_POLICY.min_score, equity)
    policy = FusionPolicy(
        window=DEFAULT_FUSION_POLICY.window,
        min_score=min_score,
        exit_min_score=DEFAULT_FUSION_POLICY.exit_min_score,
        min_signals=DEFAULT_FUSION_POLICY.min_signals,
        conflict_strength=DEFAULT_FUSION_POLICY.conflict_strength,
    )
    signals = [s for s in _signals if agent.accepts(s)]
    decision = await pipeline().derive(fusion_policy=policy).evaluate(slot.instrument_id, signals)
    agent_info: dict[str, object] = {
        "agent_id": agent.agent_id,
        "slot": slot.key,
        "generation": agent.generation,
        "equity_brl": str(equity),
        "survival_multiplier": str(size_multiplier(equity)),
        "min_score": min_score,
    }
    if decision.proposal is not None:
        shaped = shape_for_agent(
            decision.proposal,
            agent=agent,
            instrument=instrument,
            equity_brl=equity,
            brl_per_quote=fx,
        )
        sizing = {**decision.sizing, **shaped.sizing}
        if shaped.intent is None:
            decision = dataclasses.replace(
                decision,
                outcome=DecisionOutcome.ABSTAINED,
                abstain_reason=shaped.reason,
                proposal=None,
                sizing=sizing,
            )
        else:
            decision = dataclasses.replace(decision, proposal=shaped.intent, sizing=sizing)
    await _record(decision, agent=agent_info)
    if decision.proposal is None:
        return decision, None, None
    risk, order = await submit_for_agent(agent.agent_id, decision.proposal)
    return decision, risk, order


def buffer_signal(signal: Signal) -> None:
    """Hold a signal for fusion without starting an evaluation (the closed-loop drill)."""

    _signals.append(signal)


def forget_signal(signal: Signal) -> None:
    """Drop a drill signal once evaluated, so it never sways a later decision."""

    for held in list(_signals):
        if held.signal_id == signal.signal_id:
            _signals.remove(held)


async def evaluate(instrument_id: str) -> tuple[AgentDecision, RiskDecision | None, dict | None]:
    return await _evaluate(instrument_id)


def on_signal_accepted(signal: Signal) -> None:
    """Called by the signal surface after a signal was durably journaled."""

    _signals.append(signal)
    if not settings.agent_pipeline_enabled:
        return
    slot = slot_of(signal) if settings.agent_roster_enabled else None
    if settings.agent_roster_enabled and slot is None:
        return  # no agent trades this kind of signal

    async def run() -> None:
        try:
            if slot is not None:
                await _evaluate_agent(slot)
            else:
                await _evaluate(signal.instrument_id)
        except Exception:
            logger.exception("agent pipeline failed for %s", signal.instrument_id)

    task = asyncio.get_running_loop().create_task(run(), name="agent-pipeline")
    _background.add(task)
    task.add_done_callback(_background.discard)


class EvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str = Field(min_length=3, max_length=160)


@router.get("/status")
def agent_status() -> dict[str, object]:
    return {
        "enabled": settings.agent_pipeline_enabled,
        "reviewer": settings.agent_reviewer,
        "model": settings.agent_model if settings.agent_reviewer == "claude" else None,
        "stage": "PAPER",
        "buffered_signals": len(_signals),
        "decisions": sum(_outcomes.values()),
        "outcomes": dict(_outcomes),
        "journal_failures": _record_failures,
        "instruments": sorted(active_instruments()),
        "roster_enabled": settings.agent_roster_enabled,
        **_BOUNDARY,
    }


@router.get("/decisions/recent")
def recent_decisions(limit: int = 20, instrument_id: str | None = None) -> dict[str, object]:
    found = [d for d in _decisions if instrument_id is None or d.instrument_id == instrument_id]
    selected = found[-max(1, min(limit, _DECISION_CAPACITY)) :]
    return {"decisions": [d.to_dict() for d in reversed(selected)], **_BOUNDARY}


@router.post("/evaluate")
async def evaluate_now(request: EvaluateRequest) -> dict[str, object]:
    """Operator-triggered evaluation over the buffered signals (operator token required)."""

    if request.instrument_id not in active_instruments():
        raise HTTPException(422, "instrument is not supported")
    if settings.agent_roster_enabled:
        from .roster_surface import ready

        results = []
        for slot in (await ready()).slots:
            if slot.instrument_id != request.instrument_id:
                continue
            decision, risk, order = await _evaluate_agent(slot)
            results.append(
                {
                    "slot": slot.key,
                    "decision": decision.to_dict() if decision is not None else None,
                    "risk": risk.to_dict() if risk is not None else None,
                    "order": order,
                }
            )
        return {"agents": results, "evaluated_at": datetime.now(UTC).isoformat()}
    decision, risk, order = await _evaluate(request.instrument_id)
    return {
        "decision": decision.to_dict(),
        "risk": risk.to_dict() if risk is not None else None,
        "order": order,
        "evaluated_at": datetime.now(UTC).isoformat(),
    }
