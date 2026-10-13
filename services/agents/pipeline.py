"""Hybrid decision pipeline: signals -> fusion -> sized candidate -> agent review.

The output is a reviewed `TradeIntent` proposal with full lineage. It carries
no order authority: Phase 3's Risk Engine decides whether it may become an
order. Every evaluation is recorded through the injected recorder.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from services.execution import ReleaseStage, Signal, TradableInstrument, TradeIntent
from services.execution.constitution import (
    MAX_DAILY_LOSS,
    MAX_DRAWDOWN,
    MAX_GROSS_LEVERAGE,
    MAX_RISK_PER_POSITION,
)
from services.execution.venue import Quote

from .fusion import (
    DEFAULT_FUSION_POLICY,
    DEFAULT_SIZING_POLICY,
    FusionDecision,
    FusionPolicy,
    SizingPolicy,
    build_candidate,
    fuse,
)
from .review import AgentReview, AgentReviewer, ReviewContext, Verdict, apply_review


class DecisionOutcome(StrEnum):
    NO_TRADE = "NO_TRADE"  # fusion produced no direction
    ABSTAINED = "ABSTAINED"  # direction, but no tradable candidate
    APPROVED = "APPROVED"
    REDUCED = "REDUCED"
    DEFERRED = "DEFERRED"
    VETOED = "VETOED"


@dataclass(frozen=True)
class PortfolioView:
    equity: Decimal
    position_quantity: Decimal


@dataclass(frozen=True)
class AgentDecision:
    decision_id: UUID
    decided_at: datetime
    instrument_id: str
    outcome: DecisionOutcome
    fusion: FusionDecision
    candidate: TradeIntent | None
    abstain_reason: str | None
    sizing: dict[str, str]
    review: AgentReview | None
    proposal: TradeIntent | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": str(self.decision_id),
            "decided_at": self.decided_at.isoformat(),
            "instrument_id": self.instrument_id,
            "outcome": self.outcome.value,
            "fusion": self.fusion.to_dict(),
            "candidate": self.candidate.model_dump(mode="json") if self.candidate else None,
            "abstain_reason": self.abstain_reason,
            "sizing": self.sizing,
            "review": self.review.model_dump(mode="json") if self.review else None,
            "proposal": self.proposal.model_dump(mode="json") if self.proposal else None,
            "proposal_fingerprint": self.proposal.fingerprint() if self.proposal else None,
            "order_authority": False,
            "financial_connectivity": False,
            "real_money_execution": False,
        }


_OUTCOME_BY_VERDICT = {
    Verdict.APPROVE: DecisionOutcome.APPROVED,
    Verdict.REDUCE: DecisionOutcome.REDUCED,
    Verdict.DEFER: DecisionOutcome.DEFERRED,
    Verdict.VETO: DecisionOutcome.VETOED,
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


class HybridDecisionPipeline:
    def __init__(
        self,
        *,
        instruments: Mapping[str, TradableInstrument],
        reviewer: AgentReviewer,
        quotes: Callable[[str], Quote | None],
        portfolio: Callable[[str], PortfolioView],
        recorder: Callable[[AgentDecision], Awaitable[None]] | None = None,
        fusion_policy: FusionPolicy = DEFAULT_FUSION_POLICY,
        sizing_policy: SizingPolicy = DEFAULT_SIZING_POLICY,
        stage: ReleaseStage = ReleaseStage.PAPER,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if stage in (ReleaseStage.CANARY, ReleaseStage.LIVE):
            raise ValueError("the decision pipeline is not certified for real-money stages")
        self._instruments = dict(instruments)
        self._reviewer = reviewer
        self._quotes = quotes
        self._portfolio = portfolio
        self._recorder = recorder
        self._fusion_policy = fusion_policy
        self._sizing_policy = sizing_policy
        self._stage = stage
        self._clock = clock

    async def evaluate(self, instrument_id: str, signals: Iterable[Signal]) -> AgentDecision:
        now = self._clock()
        pool = list(signals)
        fusion = fuse(pool, instrument_id=instrument_id, now=now, policy=self._fusion_policy)
        instrument = self._instruments.get(instrument_id)

        def finish(outcome: DecisionOutcome, **fields: Any) -> AgentDecision:
            values: dict[str, Any] = {
                "candidate": None,
                "abstain_reason": None,
                "sizing": {},
                "review": None,
                "proposal": None,
            }
            values.update(fields)
            return AgentDecision(uuid4(), now, instrument_id, outcome, fusion, **values)

        if instrument is None:
            decision = finish(DecisionOutcome.ABSTAINED, abstain_reason="unknown instrument")
        elif fusion.direction.value == "FLAT":
            decision = finish(DecisionOutcome.NO_TRADE, abstain_reason=fusion.reason)
        else:
            quote = self._quotes(instrument_id)
            view = self._portfolio(instrument_id)
            candidate = build_candidate(
                fusion,
                instrument=instrument,
                quote=quote,
                equity=view.equity,
                position_quantity=view.position_quantity,
                now=now,
                policy=self._sizing_policy,
                stage=self._stage,
            )
            if candidate.intent is None:
                decision = finish(
                    DecisionOutcome.ABSTAINED,
                    abstain_reason=candidate.abstain_reason,
                    sizing=candidate.sizing,
                )
            else:
                context = ReviewContext(
                    candidate=candidate.intent,
                    fusion=fusion.to_dict(),
                    market=_market_context(quote, now),
                    portfolio={
                        "equity": str(view.equity),
                        "position_quantity": str(view.position_quantity),
                    },
                    signals=[
                        s.model_dump(mode="json")
                        for s in pool
                        if s.signal_id in set(fusion.signal_ids)
                    ],
                    constraints={
                        "max_risk_per_position": str(MAX_RISK_PER_POSITION),
                        "max_daily_loss": str(MAX_DAILY_LOSS),
                        "max_drawdown": str(MAX_DRAWDOWN),
                        "max_gross_leverage": str(MAX_GROSS_LEVERAGE),
                        "spot_only": True,
                        "sizing": candidate.sizing,
                    },
                )
                review = await self._reviewer.review(context)
                proposal = apply_review(candidate.intent, review, instrument)
                outcome = _OUTCOME_BY_VERDICT[review.verdict]
                if outcome is DecisionOutcome.REDUCED and proposal is None:
                    outcome = DecisionOutcome.ABSTAINED
                decision = finish(
                    outcome,
                    candidate=candidate.intent,
                    sizing=candidate.sizing,
                    review=review,
                    proposal=proposal,
                    abstain_reason=(
                        "reduced below venue minimum"
                        if outcome is DecisionOutcome.ABSTAINED
                        else None
                    ),
                )

        if self._recorder is not None:
            await self._recorder(decision)
        return decision


def _market_context(quote: Quote | None, now: datetime) -> dict[str, object]:
    if quote is None:
        return {}
    mid = (quote.bid + quote.ask) / 2
    return {
        "bid": str(quote.bid),
        "ask": str(quote.ask),
        "spread_bps": str(((quote.ask - quote.bid) / mid * 10_000).quantize(Decimal("0.01"))),
        "quote_age_seconds": round((now - quote.observed_at).total_seconds(), 3),
    }
