from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CircuitBreakerAction(StrEnum):
    CONTINUE = "CONTINUE"
    DEGRADED = "DEGRADED"
    HALT = "HALT"


class CircuitBreakerReason(StrEnum):
    STALE_DATA = "STALE_DATA"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    EVENT_BUS_UNAVAILABLE = "EVENT_BUS_UNAVAILABLE"
    RISK_UNAVAILABLE = "RISK_UNAVAILABLE"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    UNKNOWN_STATE = "UNKNOWN_STATE"


@dataclass(frozen=True, slots=True)
class CircuitBreakerDecision:
    action: CircuitBreakerAction
    reasons: tuple[CircuitBreakerReason, ...]

    @property
    def halt_required(self) -> bool:
        return self.action == CircuitBreakerAction.HALT


_HALT_REASONS = frozenset(
    {
        CircuitBreakerReason.STALE_DATA,
        CircuitBreakerReason.RISK_UNAVAILABLE,
        CircuitBreakerReason.POSITION_MISMATCH,
        CircuitBreakerReason.UNKNOWN_STATE,
    }
)


def evaluate_circuit_breakers(
    *,
    data_fresh: bool = True,
    database_available: bool = True,
    event_bus_available: bool = True,
    risk_available: bool = True,
    positions_consistent: bool = True,
    unknown_state: bool = False,
) -> CircuitBreakerDecision:
    reasons: list[CircuitBreakerReason] = []
    if not data_fresh:
        reasons.append(CircuitBreakerReason.STALE_DATA)
    if not database_available:
        reasons.append(CircuitBreakerReason.DATABASE_UNAVAILABLE)
    if not event_bus_available:
        reasons.append(CircuitBreakerReason.EVENT_BUS_UNAVAILABLE)
    if not risk_available:
        reasons.append(CircuitBreakerReason.RISK_UNAVAILABLE)
    if not positions_consistent:
        reasons.append(CircuitBreakerReason.POSITION_MISMATCH)
    if unknown_state:
        reasons.append(CircuitBreakerReason.UNKNOWN_STATE)

    if any(reason in _HALT_REASONS for reason in reasons):
        action = CircuitBreakerAction.HALT
    elif reasons:
        action = CircuitBreakerAction.DEGRADED
    else:
        action = CircuitBreakerAction.CONTINUE

    return CircuitBreakerDecision(action=action, reasons=tuple(reasons))
