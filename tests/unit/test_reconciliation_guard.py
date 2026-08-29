from services.events.reconciliation import ReconciliationIssue, ReconciliationResult
from services.events.reconciliation_guard import (
    ReconciliationAction,
    assess_reconciliation,
)


def test_consistent_reconciliation_continues() -> None:
    decision = assess_reconciliation(ReconciliationResult(consistent=True, issues=()))

    assert decision.action == ReconciliationAction.CONTINUE
    assert decision.halt_required is False
    assert decision.issues == ()


def test_any_reconciliation_divergence_requires_halt() -> None:
    result = ReconciliationResult(
        consistent=False,
        issues=(
            ReconciliationIssue.POSITION_MISMATCH,
            ReconciliationIssue.JOURNAL_MISMATCH,
        ),
    )
    decision = assess_reconciliation(result)

    assert decision.action == ReconciliationAction.HALT
    assert decision.halt_required is True
    assert decision.issues == result.issues
