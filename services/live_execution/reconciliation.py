from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ReconciliationSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class NormalizedAccountState(BaseModel):
    snapshot_id: str = Field(min_length=1, max_length=256)
    balance: float
    margin_available: float = Field(ge=0.0)
    positions: dict[str, float] = Field(default_factory=dict)
    open_orders: set[str] = Field(default_factory=set)
    observed_at: datetime


class ReconciliationEvent(BaseModel):
    correlation_id: str = Field(min_length=8, max_length=128)
    severity: ReconciliationSeverity
    component: str = Field(min_length=1, max_length=128)
    expected: object
    observed: object
    reason: str = Field(min_length=1, max_length=1_000)
    halt_required: bool
    event_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReconciliationResult(BaseModel):
    clean: bool
    halt_required: bool
    events: list[ReconciliationEvent]


class ReconciliationEngine:
    """Compares normalized local state to an external source-of-truth snapshot."""

    def reconcile(
        self,
        *,
        correlation_id: str,
        internal: NormalizedAccountState,
        source_of_truth: NormalizedAccountState,
    ) -> ReconciliationResult:
        events: list[ReconciliationEvent] = []
        self._compare(
            events,
            correlation_id,
            component="balance",
            expected=internal.balance,
            observed=source_of_truth.balance,
            critical=True,
        )
        self._compare(
            events,
            correlation_id,
            component="margin_available",
            expected=internal.margin_available,
            observed=source_of_truth.margin_available,
            critical=True,
        )
        self._compare(
            events,
            correlation_id,
            component="positions",
            expected=internal.positions,
            observed=source_of_truth.positions,
            critical=True,
        )
        self._compare(
            events,
            correlation_id,
            component="open_orders",
            expected=internal.open_orders,
            observed=source_of_truth.open_orders,
            critical=True,
        )
        halt_required = any(event.halt_required for event in events)
        return ReconciliationResult(
            clean=not events,
            halt_required=halt_required,
            events=events,
        )

    @staticmethod
    def _compare(
        events: list[ReconciliationEvent],
        correlation_id: str,
        *,
        component: str,
        expected: object,
        observed: object,
        critical: bool,
    ) -> None:
        if expected == observed:
            return
        events.append(
            ReconciliationEvent(
                correlation_id=correlation_id,
                severity=(
                    ReconciliationSeverity.CRITICAL
                    if critical
                    else ReconciliationSeverity.WARNING
                ),
                component=component,
                expected=expected,
                observed=observed,
                reason=f"{component} differs from source of truth",
                halt_required=critical,
            )
        )
