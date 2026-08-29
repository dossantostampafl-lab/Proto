from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import APIRouter, Response

from .event_state import event_runtime


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
    return asdict(event_runtime.snapshot())


@router.get("/ready")
def event_ready(response: Response) -> dict[str, object]:
    snapshot = event_runtime.snapshot()
    if not snapshot.ready:
        response.status_code = 503
    return {
        "status": "ready" if snapshot.ready else "not_ready",
        **asdict(snapshot),
    }
