from __future__ import annotations

from fastapi import APIRouter

from .app_state import portfolio, replay_session, runtime, simulator
from .models import KillSwitchState, RuntimeState, SimulationRequest, SimulationResult, SystemMode
from .settings import settings
from .simulation_policy import authoritative_simulation_request
from .websockets import hub

router = APIRouter(prefix="/shadow", tags=["shadow-control"])


@router.post("/start", response_model=RuntimeState)
async def start_shadow_mode() -> RuntimeState:
    """Enable live-market decision evaluation without mutating the paper portfolio.

    Shadow mode is intentionally non-financial. Candidate orders are passed through
    the same server-authoritative risk and fill model as paper simulation, but the
    resulting hypothetical fill is never persisted or applied to portfolio state.
    """
    if runtime.kill_switch != KillSwitchState.ARMED:
        runtime.running = False
        return runtime

    replay_session.reset()
    runtime.mode = SystemMode.SHADOW
    runtime.running = True
    await hub.broadcast(
        "analytics",
        {"type": "runtime", "data": runtime.model_dump(mode="json")},
    )
    return runtime


@router.post("/stop", response_model=RuntimeState)
async def stop_shadow_mode() -> RuntimeState:
    runtime.running = False
    await hub.broadcast(
        "analytics",
        {"type": "runtime", "data": runtime.model_dump(mode="json")},
    )
    return runtime


@router.get("/status")
def shadow_status() -> dict[str, object]:
    enabled = (
        runtime.mode == SystemMode.SHADOW
        and runtime.running
        and runtime.kill_switch == KillSwitchState.ARMED
    )
    return {
        "mode": runtime.mode,
        "running": runtime.running,
        "kill_switch": runtime.kill_switch,
        "shadow_evaluation_enabled": enabled,
        "portfolio_mutation": False,
        "fill_persistence": False,
        "financial_connectivity": False,
        "real_money_execution": False,
    }


@router.post("/evaluate")
def evaluate_shadow_decision(request: SimulationRequest) -> dict[str, object]:
    """Evaluate a candidate against authoritative risk without portfolio side effects."""
    if (
        runtime.mode != SystemMode.SHADOW
        or not runtime.running
        or runtime.kill_switch != KillSwitchState.ARMED
    ):
        result = SimulationResult(
            mode=SystemMode.SHADOW,
            accepted=False,
            reason="shadow mode halted",
        )
    else:
        effective_request = authoritative_simulation_request(
            request,
            portfolio.snapshot(),
            max_order_notional=settings.simulation_max_order_notional,
            max_position_notional=settings.simulation_max_position_notional,
            max_slippage_bps=settings.simulation_max_slippage_bps,
        )
        simulated = simulator.simulate(effective_request)
        result = simulated.model_copy(update={"mode": SystemMode.SHADOW})

    return {
        "decision": result.model_dump(mode="json"),
        "would_execute": bool(result.accepted and result.fill is not None),
        "portfolio_mutated": False,
        "fill_persisted": False,
        "financial_connectivity": False,
        "real_money_execution": False,
    }


__all__ = ["router"]
