from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter

from services.orchestration.registry import CATALOG_VERSION, JOB_CATALOG

from .app_state import orchestration_store, persistence_engine
from .orchestration_state import orchestration_supervisor
from .persistence import database_ready
from .safety_policy import policy_snapshot
from .settings import settings


@asynccontextmanager
async def orchestration_lifespan(_: APIRouter) -> AsyncIterator[None]:
    if orchestration_supervisor is not None:
        await orchestration_supervisor.start()
    try:
        yield
    finally:
        if orchestration_supervisor is not None:
            await orchestration_supervisor.stop()


router = APIRouter(
    prefix="/orchestration",
    tags=["orchestration"],
    lifespan=orchestration_lifespan,
)


@router.get("/status")
async def orchestration_status() -> dict[str, object]:
    persistence_ready = (
        await database_ready(persistence_engine) if persistence_engine is not None else False
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
