from __future__ import annotations

from .app_state import runtime
from .models import KillSwitchState, SystemMode
from .settings import settings

SIMULATED_EXECUTION_MODES = frozenset(
    {
        SystemMode.SIMULATION,
        SystemMode.SHADOW,
        SystemMode.PAPER_TRADING,
    }
)


def simulation_execution_allowed() -> bool:
    return (
        runtime.mode in SIMULATED_EXECUTION_MODES
        and runtime.running
        and runtime.kill_switch == KillSwitchState.ARMED
    )


def risk_snapshot() -> dict[str, object]:
    return {
        "kill_switch": runtime.kill_switch,
        "mode": runtime.mode,
        "simulation_allowed": simulation_execution_allowed(),
        "financial_connectivity": False,
        "real_money_execution": False,
        "minimum_net_edge": settings.minimum_net_edge,
        "minimum_confidence": settings.minimum_confidence,
        "max_notional": settings.max_notional,
        "max_daily_drawdown": settings.max_daily_drawdown,
    }


__all__ = ["SIMULATED_EXECUTION_MODES", "risk_snapshot", "simulation_execution_allowed"]
