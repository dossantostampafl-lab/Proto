from types import SimpleNamespace
from uuid import UUID, uuid4

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


class FakeCreationStore:
    def __init__(self, runs):
        self.runs = runs

    async def list_for_mission(self, mission_id: str):
        return self.runs

    async def recent_creation_runs(self, limit: int):
        return self.runs[:limit]

    async def get(self, run_id: str):
        return next((run for run in self.runs if run.id == run_id), None)


def fake_run(run_id: str, state: str, result=None, last_error=None):
    return SimpleNamespace(
        id=run_id,
        idempotency_key=f"mission:550e8400-e29b-41d4-a716-446655440000:opportunity-scan",
        job_name="opportunity-scan",
        mode="LIVE_MONITORING",
        state=SimpleNamespace(value=state),
        attempts=1,
        max_attempts=3,
        result=result,
        last_error=last_error,
        created_at=SimpleNamespace(isoformat=lambda: "2026-09-04T00:00:00+00:00"),
        updated_at=SimpleNamespace(isoformat=lambda: "2026-09-04T00:00:01+00:00"),
    )


@pytest.mark.asyncio
async def test_creation_mission_status_requires_identity_and_aggregates_jobs(monkeypatch) -> None:
    mission_id = UUID("550e8400-e29b-41d4-a716-446655440000")
    runs = [fake_run("run-1", "RUNNING"), fake_run("run-2", "SUCCEEDED", {"count": 2})]
    monkeypatch.setattr(creation_surface, "settings", Settings(creation_bridge_shared_secret="expected"))
    monkeypatch.setattr(creation_surface, "proto_brain", SimpleNamespace(store=FakeCreationStore(runs)))

    payload = await creation_surface.creation_mission_status(
        mission_id, x_proto_creation_token="expected"
    )

    assert payload["mission_id"] == str(mission_id)
    assert payload["state"] == "RUNNING"
    assert [job["id"] for job in payload["jobs"]] == ["run-1", "run-2"]
    assert payload["financial_connectivity"] is False
    assert payload["real_money_execution"] is False


@pytest.mark.asyncio
async def test_creation_job_status_returns_result_and_never_financial_execution(monkeypatch) -> None:
    run = fake_run("run-1", "SUCCEEDED", {"opportunity_count": 3})
    monkeypatch.setattr(creation_surface, "settings", Settings(creation_bridge_shared_secret="expected"))
    monkeypatch.setattr(creation_surface, "proto_brain", SimpleNamespace(store=FakeCreationStore([run])))

    payload = await creation_surface.creation_job_status("run-1", x_proto_creation_token="expected")

    assert payload["state"] == "SUCCEEDED"
    assert payload["result"] == {"opportunity_count": 3}
    assert payload["financial_connectivity"] is False
    assert payload["real_money_execution"] is False


@pytest.mark.asyncio
async def test_creation_job_status_returns_404_for_unknown_job(monkeypatch) -> None:
    monkeypatch.setattr(creation_surface, "settings", Settings(creation_bridge_shared_secret="expected"))
    monkeypatch.setattr(creation_surface, "proto_brain", SimpleNamespace(store=FakeCreationStore([])))

    with pytest.raises(HTTPException) as exc_info:
        await creation_surface.creation_job_status("missing", x_proto_creation_token="expected")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_creation_activity_exposes_recent_bridge_jobs_and_summary(monkeypatch) -> None:
    runs = [
        fake_run("run-1", "RUNNING"),
        fake_run("run-2", "SUCCEEDED", {"opportunity_count": 3}),
        fake_run("run-3", "DEAD_LETTER", None, "source unavailable"),
    ]
    monkeypatch.setattr(creation_surface, "settings", Settings(creation_bridge_shared_secret="expected"))
    monkeypatch.setattr(creation_surface, "proto_brain", SimpleNamespace(store=FakeCreationStore(runs)))

    payload = await creation_surface.creation_activity(limit=10, x_proto_creation_token="expected")

    assert payload["running_jobs"] == 1
    assert payload["failed_jobs"] == 1
    assert payload["latest_result"] == {"opportunity_count": 3}
    assert payload["latest_error"] == "source unavailable"
    assert len(payload["jobs"]) == 3
    assert payload["financial_connectivity"] is False
    assert payload["real_money_execution"] is False
