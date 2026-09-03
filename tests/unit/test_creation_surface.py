from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.api.app import creation_surface
from apps.api.app.settings import Settings
from services.orchestration.missions import Mission, MissionOrigin


def test_creation_bridge_status_never_claims_financial_execution(monkeypatch) -> None:
    monkeypatch.setattr(
        creation_surface,
        "settings",
        Settings(creation_bridge_shared_secret="test-secret"),
    )
    payload = creation_surface.creation_bridge_status()
    assert payload["configured"] is True
    assert payload["accepted_origin"] == "THE_CREATION"
    assert payload["financial_connectivity"] is False
    assert payload["real_money_execution"] is False


def test_creation_identity_fails_closed_when_unconfigured(monkeypatch) -> None:
    monkeypatch.setattr(creation_surface, "settings", Settings())
    with pytest.raises(HTTPException) as exc_info:
        creation_surface._verify_identity("anything")
    assert exc_info.value.status_code == 503


def test_creation_identity_rejects_wrong_secret(monkeypatch) -> None:
    monkeypatch.setattr(
        creation_surface,
        "settings",
        Settings(creation_bridge_shared_secret="expected"),
    )
    with pytest.raises(HTTPException) as exc_info:
        creation_surface._verify_identity("wrong")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_creation_surface_rejects_non_creation_origin_before_enqueue(monkeypatch) -> None:
    monkeypatch.setattr(
        creation_surface,
        "settings",
        Settings(creation_bridge_shared_secret="expected"),
    )
    mission = Mission(
        mission_id=uuid4(),
        origin=MissionOrigin.PROTO_INTERNAL,
        objective="health check",
        requested_jobs=("market-data-health",),
        execution_mode="LIVE_MONITORING",
    )
    with pytest.raises(HTTPException) as exc_info:
        await creation_surface.submit_creation_mission(
            mission,
            x_proto_creation_token="expected",
        )
    assert exc_info.value.status_code == 422
