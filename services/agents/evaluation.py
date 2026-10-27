"""Offline promotion gate for an AI reviewer.

Replays historical candidates with known outcomes through a reviewer and
compares the reviewed book with the deterministic baseline (every candidate
traded as sized). A reviewer is promotable only if it does not lower PnL nor
deepen drawdown over enough cases; otherwise the pipeline keeps the
deterministic reviewer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from services.execution import TradableInstrument

from .review import AgentReviewer, ReviewContext, Verdict, apply_review


@dataclass(frozen=True)
class ReplayCase:
    context: ReviewContext
    # Realized return of the trade itself over the evaluation horizon, net of
    # costs, as a fraction of its notional (e.g. Decimal("-0.012")). It is
    # already signed by the trade's side: a SELL that avoided a fall has a
    # positive return. The evaluator never flips it again.
    realized_return: Decimal


@dataclass(frozen=True)
class ReviewerEvaluation:
    reviewer: str
    cases: int
    baseline_pnl: Decimal
    reviewed_pnl: Decimal
    baseline_max_drawdown: Decimal
    reviewed_max_drawdown: Decimal
    verdicts: dict[str, int]
    promotable: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "reviewer": self.reviewer,
            "cases": self.cases,
            "baseline_pnl": str(self.baseline_pnl),
            "reviewed_pnl": str(self.reviewed_pnl),
            "baseline_max_drawdown": str(self.baseline_max_drawdown),
            "reviewed_max_drawdown": str(self.reviewed_max_drawdown),
            "verdicts": self.verdicts,
            "promotable": self.promotable,
            "reason": self.reason,
        }


def _max_drawdown(pnls: Iterable[Decimal]) -> Decimal:
    equity = peak = worst = Decimal("0")
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


async def evaluate_reviewer(
    cases: Iterable[ReplayCase],
    reviewer: AgentReviewer,
    instruments: Mapping[str, TradableInstrument],
    *,
    min_cases: int = 50,
) -> ReviewerEvaluation:
    baseline: list[Decimal] = []
    reviewed: list[Decimal] = []
    verdicts = {verdict.value: 0 for verdict in Verdict}
    for case in cases:
        intent = case.context.candidate
        price = intent.limit_price
        if price is None:
            raise ValueError("replay candidates must carry a limit price")
        per_unit = price * case.realized_return
        baseline.append(intent.quantity * per_unit)

        review = await reviewer.review(case.context)
        verdicts[review.verdict.value] += 1
        allowed = apply_review(intent, review, instruments[intent.instrument_id])
        reviewed.append(allowed.quantity * per_unit if allowed is not None else Decimal("0"))

    baseline_pnl, reviewed_pnl = sum(baseline, Decimal("0")), sum(reviewed, Decimal("0"))
    baseline_dd, reviewed_dd = _max_drawdown(baseline), _max_drawdown(reviewed)
    if len(baseline) < min_cases:
        promotable, reason = False, f"needs at least {min_cases} cases"
    elif reviewed_pnl < baseline_pnl:
        promotable, reason = False, "reviewed PnL below the deterministic baseline"
    elif reviewed_dd > baseline_dd:
        promotable, reason = False, "reviewed drawdown deeper than the deterministic baseline"
    else:
        promotable, reason = True, "no worse than the deterministic baseline"
    return ReviewerEvaluation(
        reviewer=reviewer.name,
        cases=len(baseline),
        baseline_pnl=baseline_pnl,
        reviewed_pnl=reviewed_pnl,
        baseline_max_drawdown=baseline_dd,
        reviewed_max_drawdown=reviewed_dd,
        verdicts=verdicts,
        promotable=promotable,
        reason=reason,
    )
