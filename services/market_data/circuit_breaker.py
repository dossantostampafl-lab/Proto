from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .core import DataQualityIssue, DataQualityReport


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass(frozen=True)
class CircuitDecision:
    state: CircuitState
    shadow_decisions_allowed: bool
    reason: str


class MarketDataCircuitBreaker:
    """Fail closed when data quality is unsafe for shadow decisions."""

    _fatal_issues = frozenset(
        {
            DataQualityIssue.STALE_FEED,
            DataQualityIssue.INVALID_SPREAD,
            DataQualityIssue.NEGATIVE_SIZE,
            DataQualityIssue.NEGATIVE_VOLUME,
            DataQualityIssue.NON_POSITIVE_PRICE,
            DataQualityIssue.OUT_OF_ORDER_TIMESTAMP,
        }
    )

    def __init__(self, *, recovery_successes: int = 3) -> None:
        if recovery_successes < 1:
            raise ValueError("recovery_successes must be positive")
        self.recovery_successes = recovery_successes
        self._state = CircuitState.CLOSED
        self._consecutive_valid = 0

    @property
    def state(self) -> CircuitState:
        return self._state

    def reset(self) -> None:
        self._state = CircuitState.CLOSED
        self._consecutive_valid = 0

    def evaluate(self, report: DataQualityReport) -> CircuitDecision:
        fatal = [issue for issue in report.issues if issue in self._fatal_issues]
        if fatal:
            self._state = CircuitState.OPEN
            self._consecutive_valid = 0
            return CircuitDecision(
                state=self._state,
                shadow_decisions_allowed=False,
                reason="fatal data-quality issue: " + ",".join(issue.value for issue in fatal),
            )

        if self._state == CircuitState.OPEN:
            self._state = CircuitState.HALF_OPEN
            self._consecutive_valid = 1
            return CircuitDecision(
                state=self._state,
                shadow_decisions_allowed=False,
                reason="recovery validation in progress",
            )

        if self._state == CircuitState.HALF_OPEN:
            self._consecutive_valid += 1
            if self._consecutive_valid >= self.recovery_successes:
                self._state = CircuitState.CLOSED
                return CircuitDecision(
                    state=self._state,
                    shadow_decisions_allowed=True,
                    reason="data-quality recovery validated",
                )
            return CircuitDecision(
                state=self._state,
                shadow_decisions_allowed=False,
                reason="recovery validation in progress",
            )

        return CircuitDecision(
            state=CircuitState.CLOSED,
            shadow_decisions_allowed=report.valid,
            reason="data quality healthy" if report.valid else "non-fatal data-quality issue",
        )
