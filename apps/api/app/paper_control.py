from __future__ import annotations

from fastapi import APIRouter

from .app_state import runtime
from .models import KillSwitchState, RuntimeState, SystemMode
from .paper_autopilot import paper_autopilot
from .runtime_control import start_runtime, stop_runtime

router = APIRouter(prefix="/paper", tags=["paper-control"])


@router.post("/start", response_model=RuntimeState)
async def start_paper_trading() -> RuntimeState:
    """Enable the existing server-authoritative paper simulator.

    This changes only the internal simulation runtime. The public read-only live
    market monitor remains independent and continues to provide BTC/ETH/SOL
    quotes. No financial connectivity is created by this transition.
    """
    return await start_runtime(SystemMode.PAPER_TRADING)


@router.post("/stop", response_model=RuntimeState)
async def stop_paper_trading() -> RuntimeState:
    """Stop paper execution and disarm its persistent server autopilot."""
    if paper_autopilot.running:
        await paper_autopilot.stop()
    return await stop_runtime()


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
