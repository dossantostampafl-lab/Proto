from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Query

from services.orchestration.registry import CATALOG_VERSION, JOB_CATALOG

from .app_state import (
    decision_memory_store,
    orchestration_engine,
    orchestration_store,
    persistence_engine,
)
from .orchestration_state import orchestration_supervisor
from .persistence import database_ready
from .safety_policy import policy_snapshot
from .settings import settings


@asynccontextmanager
async def orchestration_lifespan(_: APIRouter) -> AsyncIterator[None]:
    if orchestration_store is not None:
        await orchestration_store.init_schema()
    if decision_memory_store is not None:
        await decision_memory_store.init_schema()
    if orchestration_supervisor is not None:
        await orchestration_supervisor.start()
    try:
        yield
    finally:
        if orchestration_supervisor is not None:
            await orchestration_supervisor.stop()
        if orchestration_engine is not None and orchestration_engine is not persistence_engine:
            await orchestration_engine.dispose()


router = APIRouter(
    prefix="/orchestration",
    tags=["orchestration"],
    lifespan=orchestration_lifespan,
)


@router.get("/status")
async def orchestration_status() -> dict[str, object]:
    persistence_ready = (
        await database_ready(orchestration_engine) if orchestration_engine is not None else False
    )
    safety = policy_snapshot(settings.system_mode)
    safe_scope_ready = orchestration_store is not None and persistence_ready
    supervisor_status = (
        orchestration_supervisor.status()
        if orchestration_supervisor is not None
        else {
            "running": False,
            "registered_jobs": [],
            "scheduled_jobs": [],
            "financial_connectivity": False,
            "real_money_execution": False,
        }
    )

    return {
        "catalog_version": CATALOG_VERSION,
        "contracts": [
            {
                "name": name,
                "capability": contract.spec.capability.value,
                "owner_domain": contract.owner_domain,
                "allowed_modes": sorted(contract.spec.allowed_modes),
                "requires_risk_gate": contract.spec.requires_risk_gate,
                "completion_criteria": list(contract.completion_criteria),
            }
            for name, contract in sorted(JOB_CATALOG.items())
        ],
        "durable_runtime": {
            "configured": orchestration_store is not None,
            "persistence_ready": persistence_ready,
            "decision_memory_configured": decision_memory_store is not None,
            "general_simulation_persistence_enabled": settings.persistence_enabled,
            "orchestration_persistence_enabled": settings.orchestration_persistence_enabled,
        },
        "supervisor": supervisor_status,
        "readiness": {
            "ready_safe_scope": safe_scope_ready,
            "live_ready": False,
            "live_ready_reason": "real financial execution is outside the approved PROTO scope",
        },
        "safety": {
            **safety,
            "human_activation_required_for_future_live_execution": True,
            "live_canary_max_notional": 0,
        },
        "control_plane": {
            "read_only_surface": True,
            "arbitrary_job_execution_endpoint": False,
        },
    }


@router.get("/decision-memory/status")
async def decision_memory_status() -> dict[str, object]:
    if decision_memory_store is None:
        return {
            "configured": False,
            "records": 0,
            "resolved": 0,
            "unresolved": 0,
            "financial_connectivity": False,
            "real_money_execution": False,
        }
    snapshot = await decision_memory_store.snapshot()
    return {"configured": True, **snapshot}


@router.get("/decision-memory/recent")
async def recent_decisions(
    instrument_id: str | None = Query(default=None, min_length=3, max_length=160),
    limit: int = Query(default=100, ge=1, le=1_000),
) -> dict[str, object]:
    if decision_memory_store is None:
        return {
            "configured": False,
            "instrument_id": instrument_id.strip().upper() if instrument_id else None,
            "count": 0,
            "decisions": [],
            "financial_connectivity": False,
            "real_money_execution": False,
        }

    rows = await decision_memory_store.recent(
        instrument_id=instrument_id,
        limit=limit,
    )
    decisions = [
        {
            "decision": entry.model_dump(mode="json"),
            "outcome": outcome.model_dump(mode="json") if outcome is not None else None,
        }
        for entry, outcome in rows
    ]
    return {
        "configured": True,
        "instrument_id": instrument_id.strip().upper() if instrument_id else None,
        "count": len(decisions),
        "decisions": decisions,
        "financial_connectivity": False,
        "real_money_execution": False,
    }
