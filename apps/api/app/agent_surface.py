"""Hybrid agent pipeline surface (Phase 2). Decisions are proposals with no order authority.

Accepted signals are buffered here; when the pipeline is enabled each new
signal triggers an evaluation for its instrument. Every decision is written to
the operational journal as AGENT_DECISION. Positions are taken as zero and
equity as the constitutional reference capital until the Phase 4 ledger exists,
so SHORT signals abstain and sizing never exceeds the reference budget.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter, deque
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.agents import (
    AgentDecision,
    AgentReview,
    ClaudeReviewer,
    DeterministicReviewer,
    HybridDecisionPipeline,
    PortfolioView,
    ReviewContext,
    SizingPolicy,
)
from services.agents.review import defer
from services.execution import Signal
from services.execution.catalog import PAPER_INSTRUMENTS
from services.execution.constitution import REFERENCE_CAPITAL
from services.execution.venue import Quote

from .event_state import JournalUnavailable, record_operational_event
from .live_monitor import live_monitor
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
    """Live public quote for BINANCE_SPOT:<BASE>USDT when the monitor streams Binance."""

    venue, _, symbol = instrument_id.partition(":")
    if venue != "BINANCE_SPOT" or not symbol.endswith("USDT"):
        return None
    if settings.live_market_source != "BINANCE":
        return None
    snapshot = live_monitor.snapshot(symbol.removesuffix("USDT"))
    if snapshot is None or str(snapshot.get("venue", "")).upper() != "BINANCE":
        return None
    try:
        return Quote(
            bid=Decimal(str(snapshot["bid"])),
            ask=Decimal(str(snapshot["ask"])),
            observed_at=datetime.fromisoformat(str(snapshot["timestamp"])),
        )
    except (KeyError, InvalidOperation, ValueError):
        return None


def _portfolio(_instrument_id: str) -> PortfolioView:
    return PortfolioView(equity=REFERENCE_CAPITAL, position_quantity=Decimal("0"))


async def _record(decision: AgentDecision) -> None:
    global _record_failures
    _decisions.append(decision)
    _outcomes[decision.outcome.value] += 1
    try:
        await record_operational_event(
            event_type="AGENT_DECISION",
            source="agent-pipeline",
            payload=decision.to_dict(),
            idempotency_key=f"agent-decision:{decision.decision_id}",
        )
    except JournalUnavailable:
        _record_failures += 1
        logger.exception("agent decision %s was not journaled", decision.decision_id)


def pipeline() -> HybridDecisionPipeline:
    global _pipeline, _pipeline_settings_id
    if _pipeline is None or _pipeline_settings_id != id(settings):
        _pipeline = HybridDecisionPipeline(
            instruments=PAPER_INSTRUMENTS,
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
    _pipeline = None
    _pipeline_settings_id = None
    _record_failures = 0


async def _evaluate(instrument_id: str) -> AgentDecision:
    return await pipeline().evaluate(instrument_id, list(_signals))


def on_signal_accepted(signal: Signal) -> None:
    """Called by the signal surface after a signal was durably journaled."""

    _signals.append(signal)
    if not settings.agent_pipeline_enabled:
        return

    async def run() -> None:
        try:
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
        "instruments": sorted(PAPER_INSTRUMENTS),
        **_BOUNDARY,
    }


@router.get("/decisions/recent")
def recent_decisions(limit: int = 20) -> dict[str, object]:
    selected = list(_decisions)[-max(1, min(limit, _DECISION_CAPACITY)) :]
    return {"decisions": [d.to_dict() for d in reversed(selected)], **_BOUNDARY}


@router.post("/evaluate")
async def evaluate_now(request: EvaluateRequest) -> dict[str, object]:
    """Operator-triggered evaluation over the buffered signals (operator token required)."""

    if request.instrument_id not in PAPER_INSTRUMENTS:
        raise HTTPException(422, "instrument is not supported")
    decision = await _evaluate(request.instrument_id)
    return {"decision": decision.to_dict(), "evaluated_at": datetime.now(UTC).isoformat()}
