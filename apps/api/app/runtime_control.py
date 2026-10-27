"""Shared runtime start/stop transitions and their analytics broadcast."""

from __future__ import annotations

from fastapi import HTTPException

from .app_state import replay_session, runtime
from .models import KillSwitchState, RuntimeState, SystemMode
from .websockets import hub


async def broadcast_runtime() -> None:
    await hub.broadcast(
        "analytics",
        {"type": "runtime", "data": runtime.model_dump(mode="json")},
    )


async def start_runtime(mode: SystemMode) -> RuntimeState:
    if runtime.kill_switch != KillSwitchState.ARMED:
        # Refused, like /replay/start: a 200 here read (and was journaled) as a start that
        # never happened.
        runtime.running = False
        raise HTTPException(status_code=409, detail="kill switch is not armed")

    replay_session.reset()
    runtime.mode = mode
    runtime.running = True
    await broadcast_runtime()
    return runtime


async def stop_runtime() -> RuntimeState:
    runtime.running = False
    await broadcast_runtime()
    return runtime
