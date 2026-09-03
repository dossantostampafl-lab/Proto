from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException, status

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
