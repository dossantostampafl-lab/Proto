from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class ReconciliationIssue(StrEnum):
    FILL_WITHOUT_ORDER = "FILL_WITHOUT_ORDER"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    JOURNAL_MISMATCH = "JOURNAL_MISMATCH"


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    consistent: bool
    issues: tuple[ReconciliationIssue, ...]


def reconcile(
    *,
    order_ids: set[str],
    fill_order_ids: set[str],
    expected_positions: dict[str, float],
    actual_positions: dict[str, float],
    journal_event_count: int,
    persisted_event_count: int,
    tolerance: float = 1e-9,
) -> ReconciliationResult:
    if not isfinite(tolerance) or tolerance < 0.0:
        raise ValueError("tolerance must be finite and non-negative")

    issues: list[ReconciliationIssue] = []

    if not fill_order_ids.issubset(order_ids):
        issues.append(ReconciliationIssue.FILL_WITHOUT_ORDER)

    assets = set(expected_positions) | set(actual_positions)
    for asset in assets:
        expected = expected_positions.get(asset, 0.0)
        actual = actual_positions.get(asset, 0.0)
        if not isfinite(expected) or not isfinite(actual):
            issues.append(ReconciliationIssue.POSITION_MISMATCH)
            break
        if abs(expected - actual) > tolerance:
            issues.append(ReconciliationIssue.POSITION_MISMATCH)
            break

    if journal_event_count != persisted_event_count:
        issues.append(ReconciliationIssue.JOURNAL_MISMATCH)

    return ReconciliationResult(consistent=not issues, issues=tuple(issues))
