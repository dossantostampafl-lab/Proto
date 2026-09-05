from __future__ import annotations

import hmac
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, status

from services.orchestration.creation_queries import list_creation_mission_runs, recent_creation_runs
from services.orchestration.missions import Mission, MissionGateway, MissionOrigin, MissionReceipt

from .orchestration_state import proto_brain
from .settings import settings

router = APIRouter(prefix="/creation", tags=["creation-bridge"])

_ALLOWED_CREATION_JOBS = frozenset(
    {
        "market-data-health",
        "opportunity-scan",
        "shadow-decision",
    }
)


def _gateway() -> MissionGateway:
    if proto_brain is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PROTO durable orchestration runtime is unavailable",
        )
    return MissionGateway(proto_brain, _ALLOWED_CREATION_JOBS)


def _verify_identity(token: str | None) -> None:
    expected = settings.creation_bridge_shared_secret
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The Creation bridge is not configured",
        )
    if token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The Creation service identity was not verified",
        )


def _job_payload(run: Any) -> dict[str, object]:
    state = getattr(run.state, "value", str(run.state))
    return {
        "id": run.id,
        "job_name": run.job_name,
        "mode": run.mode,
        "state": state,
        "attempts": run.attempts,
        "max_attempts": run.max_attempts,
        "result": run.result,
        "last_error": run.last_error,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "financial_connectivity": False,
        "real_money_execution": False,
    }


def _mission_state(jobs: list[dict[str, object]]) -> str:
    states = {str(job["state"]) for job in jobs}
    if states and states <= {"SUCCEEDED"}:
        return "COMPLETED"
    if "BLOCKED" in states:
        return "BLOCKED"
    if "DEAD_LETTER" in states:
        return "DEGRADED"
    return "RUNNING"


@router.get("/status")
def creation_bridge_status() -> dict[str, object]:
    return {
        "configured": settings.creation_bridge_configured,
        "orchestration_available": proto_brain is not None,
        "allowed_jobs": sorted(_ALLOWED_CREATION_JOBS),
        "accepted_origin": MissionOrigin.THE_CREATION.value,
        "transport": "HTTP_SHARED_SECRET",
        "financial_connectivity": False,
        "real_money_execution": False,
    }


@router.post("/missions", response_model=MissionReceipt)
async def submit_creation_mission(
    mission: Mission,
    x_proto_creation_token: str | None = Header(default=None),
) -> MissionReceipt:
    _verify_identity(x_proto_creation_token)
    if mission.origin is not MissionOrigin.THE_CREATION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="creation bridge only accepts THE_CREATION mission origin",
        )
    gateway = _gateway()
    return await gateway.accept(mission, identity_verified=True)


@router.get("/activity")
async def creation_activity(
    limit: int = Query(default=50, ge=1, le=200),
    x_proto_creation_token: str | None = Header(default=None),
) -> dict[str, object]:
    _verify_identity(x_proto_creation_token)
    brain = _gateway().brain
    runs = await recent_creation_runs(brain.store, limit)
    jobs = [_job_payload(run) for run in runs]
    running_states = {"QUEUED", "RUNNING", "RETRY_WAIT"}
    failed_states = {"BLOCKED", "DEAD_LETTER"}
    running_jobs = sum(1 for job in jobs if str(job["state"]) in running_states)
    failed_jobs = sum(1 for job in jobs if str(job["state"]) in failed_states)
    latest_result = next((job["result"] for job in jobs if job["result"] is not None), None)
    latest_error = next((str(job["last_error"]) for job in jobs if job["last_error"]), None)
    return {
        "running_jobs": running_jobs,
        "failed_jobs": failed_jobs,
        "latest_result": latest_result,
        "latest_error": latest_error,
        "jobs": jobs,
        "financial_connectivity": False,
        "real_money_execution": False,
    }


@router.get("/missions/{mission_id}")
async def creation_mission_status(
    mission_id: UUID,
    x_proto_creation_token: str | None = Header(default=None),
) -> dict[str, object]:
    _verify_identity(x_proto_creation_token)
    brain = _gateway().brain
    runs = await list_creation_mission_runs(brain.store, str(mission_id))
    if not runs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Creation mission not found")
    jobs = [_job_payload(run) for run in runs]
    return {
        "mission_id": str(mission_id),
        "state": _mission_state(jobs),
        "jobs": jobs,
        "financial_connectivity": False,
        "real_money_execution": False,
    }


@router.get("/jobs/{run_id}")
async def creation_job_status(
    run_id: str,
    x_proto_creation_token: str | None = Header(default=None),
) -> dict[str, object]:
    _verify_identity(x_proto_creation_token)
    brain = _gateway().brain
    run = await brain.store.get(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Creation job run not found")
    return _job_payload(run)
