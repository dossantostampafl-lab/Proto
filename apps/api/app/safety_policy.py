from __future__ import annotations

from dataclasses import dataclass


_ALLOWED_MODES = frozenset({"SIMULATION", "PAPER_TRADING", "HISTORICAL_REPLAY"})
_FORBIDDEN_MODE_TOKENS = frozenset({"LIVE", "REAL_MONEY", "PRODUCTION_TRADING"})


class SafetyPolicyError(RuntimeError):
    """Raised when runtime configuration leaves the synthetic-only safety boundary."""


@dataclass(frozen=True, slots=True)
class SafetyPolicyState:
    synthetic_only: bool
    allowed_modes: tuple[str, ...]
    real_money_execution: bool


def validate_sandbox_mode(system_mode: str) -> SafetyPolicyState:
    normalized = system_mode.strip().upper()
    if normalized in _FORBIDDEN_MODE_TOKENS or normalized not in _ALLOWED_MODES:
        raise SafetyPolicyError(
            "runtime mode is outside the synthetic-only safety boundary"
        )
    return SafetyPolicyState(
        synthetic_only=True,
        allowed_modes=tuple(sorted(_ALLOWED_MODES)),
        real_money_execution=False,
    )


def policy_snapshot(system_mode: str) -> dict[str, object]:
    state = validate_sandbox_mode(system_mode)
    return {
        "synthetic_only": state.synthetic_only,
        "allowed_modes": list(state.allowed_modes),
        "real_money_execution": state.real_money_execution,
    }
