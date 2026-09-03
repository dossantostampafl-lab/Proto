from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from services.orchestration.missions import (
    Mission,
    MissionGateway,
    MissionOrigin,
    MissionState,
)
from services.orchestration.runtime import JobCapability, JobSpec, ProtoBrain
from services.orchestration.store import SqlJobStore


@pytest_asyncio.fixture
async def store() -> SqlJobStore:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    job_store = SqlJobStore(engine)
    await job_store.init_schema()
    try:
        yield job_store
    finally:
        await engine.dispose()


def _mission(*, jobs: tuple[str, ...], mode: str = "LIVE_MONITORING") -> Mission:
    return Mission(
        mission_id=uuid4(),
        origin=MissionOrigin.THE_CREATION,
        objective="Evaluate current system state",
        requested_jobs=jobs,
        execution_mode=mode,
    )


@pytest.mark.asyncio
async def test_unverified_creation_identity_cannot_enqueue(store: SqlJobStore) -> None:
    async def handler(payload: dict[str, object]) -> dict[str, object]:
        return payload

    brain = ProtoBrain(store=store, worker_id="mission-worker")
    brain.register(JobSpec("observability", JobCapability.OBSERVABILITY), handler)
    gateway = MissionGateway(brain, frozenset({"observability"}))

    receipt = await gateway.accept(
        _mission(jobs=("observability",)),
        identity_verified=False,
    )

    assert receipt.state is MissionState.REJECTED
    assert receipt.job_run_ids == ()
    assert await brain.run_once() is None


@pytest.mark.asyncio
async def test_allowlisted_creation_mission_becomes_idempotent_job(store: SqlJobStore) -> None:
    async def handler(payload: dict[str, object]) -> dict[str, object]:
        return {"mission_id": payload["mission_id"]}

    brain = ProtoBrain(store=store, worker_id="mission-worker")
    brain.register(JobSpec("observability", JobCapability.OBSERVABILITY), handler)
    gateway = MissionGateway(brain, frozenset({"observability"}))
    mission = _mission(jobs=("observability",))

    first = await gateway.accept(mission, identity_verified=True)
    second = await gateway.accept(mission, identity_verified=True)

    assert first.state is MissionState.ACCEPTED
    assert second.state is MissionState.ACCEPTED
    assert first.job_run_ids == second.job_run_ids
    assert first.financial_connectivity is False
    assert first.real_money_execution is False


@pytest.mark.asyncio
async def test_creation_cannot_request_job_outside_bridge_allowlist(store: SqlJobStore) -> None:
    async def handler(payload: dict[str, object]) -> dict[str, object]:
        return payload

    brain = ProtoBrain(store=store, worker_id="mission-worker")
    brain.register(JobSpec("quant", JobCapability.QUANT_RESEARCH), handler)
    gateway = MissionGateway(brain, frozenset({"observability"}))

    receipt = await gateway.accept(_mission(jobs=("quant",)), identity_verified=True)

    assert receipt.state is MissionState.REJECTED
    assert receipt.job_run_ids == ()


@pytest.mark.asyncio
async def test_mission_mode_must_match_job_contract(store: SqlJobStore) -> None:
    async def handler(payload: dict[str, object]) -> dict[str, object]:
        return payload

    brain = ProtoBrain(store=store, worker_id="mission-worker")
    brain.register(
        JobSpec(
            "replay",
            JobCapability.REPLAY,
            allowed_modes=frozenset({"HISTORICAL_REPLAY"}),
        ),
        handler,
    )
    gateway = MissionGateway(brain, frozenset({"replay"}))

    receipt = await gateway.accept(
        _mission(jobs=("replay",), mode="LIVE_MONITORING"),
        identity_verified=True,
    )

    assert receipt.state is MissionState.BLOCKED
    assert receipt.job_run_ids == ()


@pytest.mark.asyncio
async def test_creation_can_request_allowlisted_shadow_job(store: SqlJobStore) -> None:
    async def handler(payload: dict[str, object]) -> dict[str, object]:
        return payload

    brain = ProtoBrain(store=store, worker_id="mission-worker")
    brain.register(
        JobSpec(
            "shadow-decision",
            JobCapability.QUANT_RESEARCH,
            allowed_modes=frozenset({"SHADOW"}),
        ),
        handler,
    )
    gateway = MissionGateway(brain, frozenset({"shadow-decision"}))

    receipt = await gateway.accept(
        _mission(jobs=("shadow-decision",), mode="SHADOW"),
        identity_verified=True,
    )

    assert receipt.state is MissionState.ACCEPTED
    assert receipt.accepted_jobs == ("shadow-decision",)
    assert len(receipt.job_run_ids) == 1
    assert receipt.financial_connectivity is False
    assert receipt.real_money_execution is False


def test_mission_contract_rejects_live_execution_mode() -> None:
    with pytest.raises(ValidationError, match="safe PROTO mode"):
        _mission(jobs=("observability",), mode="LIVE_EXECUTION")
