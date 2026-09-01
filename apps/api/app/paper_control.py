from __future__ import annotations

from fastapi import APIRouter

from .app_state import replay_session, runtime
from .models import KillSwitchState, RuntimeState, SystemMode
from .paper_autopilot import paper_autopilot
from .websockets import hub

router = APIRouter(prefix="/paper", tags=["paper-control"])


@router.post("/start", response_model=RuntimeState)
async def start_paper_trading() -> RuntimeState:
    """Enable the existing server-authoritative paper simulator.

    This changes only the internal simulation runtime. The public read-only live
    market monitor remains independent and continues to provide BTC/ETH/SOL
    quotes. No financial connectivity is created by this transition.
    """
    if runtime.kill_switch != KillSwitchState.ARMED:
        runtime.running = False
        return runtime

    replay_session.reset()
    runtime.mode = SystemMode.PAPER_TRADING
    runtime.running = True
    await hub.broadcast(
        "analytics",
        {"type": "runtime", "data": runtime.model_dump(mode="json")},
    )
    return runtime


@router.post("/stop", response_model=RuntimeState)
async def stop_paper_trading() -> RuntimeState:
    """Stop paper execution and disarm its persistent server autopilot."""
    if paper_autopilot.running:
        await paper_autopilot.stop()
    runtime.running = False
    await hub.broadcast(
        "analytics",
        {"type": "runtime", "data": runtime.model_dump(mode="json")},
    )
    return runtime


@router.get("/status")
def paper_status() -> dict[str, object]:
    return {
        "mode": runtime.mode,
        "running": runtime.running,
        "kill_switch": runtime.kill_switch,
        "paper_execution_enabled": (
            runtime.mode == SystemMode.PAPER_TRADING
            and runtime.running
            and runtime.kill_switch == KillSwitchState.ARMED
        ),
        "autopilot_running": paper_autopilot.running,
        "financial_connectivity": False,
        "real_money_execution": False,
    }


__all__ = ["router"]
