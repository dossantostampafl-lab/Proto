from dataclasses import dataclass

_ALLOWED_MODES = frozenset(
    {"SIMULATION", "PAPER_TRADING", "HISTORICAL_REPLAY", "LIVE_MONITORING"}
)
_FORBIDDEN_MODE_TOKENS = frozenset({"LIVE_TRADING", "REAL_MONEY", "PRODUCTION_TRADING"})


class SafetyPolicyError(RuntimeError):
    """Raised when runtime configuration violates the no-financial-connectivity boundary."""


@dataclass(frozen=True, slots=True)
class SafetyPolicyState:
    synthetic_only: bool
    live_market_data: bool
    financial_connectivity: bool
    allowed_modes: tuple[str, ...]
    real_money_execution: bool


def validate_runtime_mode(system_mode: str) -> SafetyPolicyState:
    normalized = system_mode.strip().upper()
    if normalized in _FORBIDDEN_MODE_TOKENS or normalized not in _ALLOWED_MODES:
        raise SafetyPolicyError(
            "runtime mode violates the no-financial-connectivity safety boundary"
        )
    live_market_data = normalized == "LIVE_MONITORING"
    return SafetyPolicyState(
        synthetic_only=not live_market_data,
        live_market_data=live_market_data,
        financial_connectivity=False,
        allowed_modes=tuple(sorted(_ALLOWED_MODES)),
        real_money_execution=False,
    )


def validate_sandbox_mode(system_mode: str) -> SafetyPolicyState:
    """Backward-compatible runtime validator retained for existing imports."""
    return validate_runtime_mode(system_mode)


def policy_snapshot(system_mode: str) -> dict[str, object]:
    state = validate_runtime_mode(system_mode)
    return {
        "synthetic_only": state.synthetic_only,
        "live_market_data": state.live_market_data,
        "financial_connectivity": state.financial_connectivity,
        "allowed_modes": list(state.allowed_modes),
        "real_money_execution": state.real_money_execution,
    }
