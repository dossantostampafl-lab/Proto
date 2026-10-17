from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import APIRouter, Query, Response

from .event_state import event_runtime, operational_journal, operational_journal_snapshot


@asynccontextmanager
async def event_router_lifespan(_: APIRouter) -> AsyncIterator[None]:
    await event_runtime.start()
    try:
        yield
    finally:
        await event_runtime.close()


router = APIRouter(
    prefix="/events",
    tags=["event-runtime"],
    lifespan=event_router_lifespan,
)


@router.get("/status")
def event_status() -> dict[str, object]:
    return {
        **asdict(event_runtime.snapshot()),
        "journal": operational_journal_snapshot(limit=1),
    }


@router.get("/ready")
def event_ready(response: Response) -> dict[str, object]:
    snapshot = event_runtime.snapshot()
    if not snapshot.ready:
        response.status_code = 503
    return {
        "status": "ready" if snapshot.ready else "not_ready",
        **asdict(snapshot),
    }


@router.get("/journal")
def event_journal(limit: int = Query(default=100, ge=1, le=1_000)) -> dict[str, object]:
    return operational_journal_snapshot(limit=limit)


@router.get("/journal/verify")
async def event_journal_verify(response: Response) -> dict[str, object]:
    """Re-verify the whole durable history from GENESIS (or the memory window)."""
    from . import control_state

    result = await operational_journal.verify()
    if not result["chain_valid"]:
        response.status_code = 409
    control = control_state.current().snapshot()
    # Truthful label: durable only when the history lives in a reachable, persistent database.
    return {**result, "durable": bool(control["durable"]), "control_plane": control}
