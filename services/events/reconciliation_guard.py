from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .reconciliation import ReconciliationIssue, ReconciliationResult


class ReconciliationAction(StrEnum):
    CONTINUE = "CONTINUE"
    HALT = "HALT"


@dataclass(frozen=True, slots=True)
class ReconciliationGuardDecision:
    action: ReconciliationAction
    issues: tuple[ReconciliationIssue, ...]

    @property
    def halt_required(self) -> bool:
        return self.action == ReconciliationAction.HALT


def assess_reconciliation(result: ReconciliationResult) -> ReconciliationGuardDecision:
    return ReconciliationGuardDecision(
        action=(
            ReconciliationAction.CONTINUE
            if result.consistent
            else ReconciliationAction.HALT
        ),
        issues=result.issues,
    )
