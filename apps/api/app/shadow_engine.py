from __future__ import annotations

from .app_state import portfolio, runtime, simulator
from .models import KillSwitchState, SimulationRequest, SimulationResult, SystemMode
from .settings import settings
from .simulation_policy import authoritative_simulation_request


def evaluate_shadow_candidate(request: SimulationRequest) -> SimulationResult:
    """Evaluate a SHADOW candidate using production risk authority without side effects.

    The helper deliberately reuses the same server-authoritative exposure, liquidity,
    volatility, drawdown and slippage limits as PAPER/SIMULATION. It never appends a
    fill journal entry and never applies a fill to the paper portfolio.
    """
    if (
        runtime.mode != SystemMode.SHADOW
        or not runtime.running
        or runtime.kill_switch != KillSwitchState.ARMED
    ):
        return SimulationResult(
            mode=SystemMode.SHADOW,
            accepted=False,
            reason="shadow mode halted",
        )

    effective_request = authoritative_simulation_request(
        request,
        portfolio.snapshot(),
        max_order_notional=settings.simulation_max_order_notional,
        max_position_notional=settings.simulation_max_position_notional,
        max_slippage_bps=settings.simulation_max_slippage_bps,
    )
    simulated = simulator.simulate(effective_request)
    return simulated.model_copy(update={"mode": SystemMode.SHADOW})


__all__ = ["evaluate_shadow_candidate"]
