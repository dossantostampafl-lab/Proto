from __future__ import annotations

from .app_state import runtime
from .models import KillSwitchState
from .settings import settings


def risk_snapshot() -> dict[str, object]:
    return {
        "kill_switch": runtime.kill_switch,
        "simulation_allowed": runtime.running
        and runtime.kill_switch == KillSwitchState.ARMED,
        "financial_connectivity": False,
        "real_money_execution": False,
        "minimum_net_edge": settings.minimum_net_edge,
        "minimum_confidence": settings.minimum_confidence,
        "max_notional": settings.max_notional,
        "max_daily_drawdown": settings.max_daily_drawdown,
    }


__all__ = ["risk_snapshot"]
