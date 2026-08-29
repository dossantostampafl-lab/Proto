from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ChaosScenario(StrEnum):
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    LATENCY_SPIKE = "LATENCY_SPIKE"
    TRANSPORT_DISCONNECT = "TRANSPORT_DISCONNECT"
    MALFORMED_PAYLOAD = "MALFORMED_PAYLOAD"


@dataclass(frozen=True, slots=True)
class FaultInjection:
    scenario: ChaosScenario
    enabled: bool = True
    latency_ms: int = 0
    stale_by_ms: int = 0
    sequence_gap: int = 0

    def validate(self) -> None:
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        if self.stale_by_ms < 0:
            raise ValueError("stale_by_ms must be non-negative")
        if self.sequence_gap < 0:
            raise ValueError("sequence_gap must be non-negative")
        if self.scenario == ChaosScenario.LATENCY_SPIKE and self.latency_ms == 0:
            raise ValueError("LATENCY_SPIKE requires latency_ms > 0")
        if self.scenario == ChaosScenario.STALE_MARKET_DATA and self.stale_by_ms == 0:
            raise ValueError("STALE_MARKET_DATA requires stale_by_ms > 0")
        if self.scenario == ChaosScenario.SEQUENCE_GAP and self.sequence_gap == 0:
            raise ValueError("SEQUENCE_GAP requires sequence_gap > 0")


@dataclass(frozen=True, slots=True)
class ChaosResult:
    scenario: ChaosScenario
    payload: dict[str, Any]
    simulated_latency_ms: int
    transport_connected: bool
    should_dead_letter: bool


def inject_fault(payload: dict[str, Any], fault: FaultInjection) -> ChaosResult:
    """Apply deterministic, simulation-only faults without sleeping or touching external systems."""
    fault.validate()
    copied = dict(payload)

    if not fault.enabled:
        return ChaosResult(
            scenario=fault.scenario,
            payload=copied,
            simulated_latency_ms=0,
            transport_connected=True,
            should_dead_letter=False,
        )

    latency_ms = 0
    transport_connected = True
    should_dead_letter = False

    if fault.scenario == ChaosScenario.STALE_MARKET_DATA:
        copied["stale_by_ms"] = fault.stale_by_ms
        copied["is_stale"] = True
    elif fault.scenario == ChaosScenario.DUPLICATE_EVENT:
        copied["duplicate"] = True
    elif fault.scenario == ChaosScenario.SEQUENCE_GAP:
        current = int(copied.get("sequence", 0))
        copied["sequence"] = current + fault.sequence_gap + 1
        copied["sequence_gap"] = fault.sequence_gap
    elif fault.scenario == ChaosScenario.LATENCY_SPIKE:
        latency_ms = fault.latency_ms
    elif fault.scenario == ChaosScenario.TRANSPORT_DISCONNECT:
        transport_connected = False
    elif fault.scenario == ChaosScenario.MALFORMED_PAYLOAD:
        copied.clear()
        copied["malformed"] = True
        should_dead_letter = True

    return ChaosResult(
        scenario=fault.scenario,
        payload=copied,
        simulated_latency_ms=latency_ms,
        transport_connected=transport_connected,
        should_dead_letter=should_dead_letter,
    )
