from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import APIRouter, Query, Response

from .event_state import event_runtime, operational_journal_snapshot


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
def event_journal_verify() -> dict[str, object]:
    snapshot = operational_journal_snapshot(limit=1)
    return {
        "status": "valid" if snapshot["chain_valid"] else "invalid",
        "chain_valid": snapshot["chain_valid"],
        "event_count": snapshot["count"],
        "persistence_enabled": snapshot["persistence_enabled"],
        "persisted_count": snapshot["persisted_count"],
        "persistence_failures": snapshot["persistence_failures"],
    }
