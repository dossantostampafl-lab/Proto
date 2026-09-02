from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from services.orchestration import JobCapability, JobSpec, JobState, ProtoBrain, SqlJobStore


@pytest_asyncio.fixture
async def store() -> SqlJobStore:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    job_store = SqlJobStore(engine)
    await job_store.init_schema()
    try:
        yield job_store
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_enqueue_is_idempotent(store: SqlJobStore) -> None:
    async def handler(payload: dict[str, object]) -> dict[str, object]:
        return payload

    brain = ProtoBrain(store=store, worker_id="worker-1")
    brain.register(JobSpec("market-data", JobCapability.MARKET_DATA), handler)

    first = await brain.enqueue(
        "market-data",
        idempotency_key="tick:btc:1",
        mode="LIVE_MONITORING",
        payload={"symbol": "BTC"},
    )
    second = await brain.enqueue(
        "market-data",
        idempotency_key="tick:btc:1",
        mode="LIVE_MONITORING",
        payload={"symbol": "BTC"},
    )

    assert first.id == second.id
    assert first.state is JobState.QUEUED


@pytest.mark.asyncio
async def test_idempotency_key_cannot_cross_job_contracts(store: SqlJobStore) -> None:
    async def handler(payload: dict[str, object]) -> dict[str, object]:
        return payload

    brain = ProtoBrain(store=store, worker_id="worker-1")
    brain.register(JobSpec("market-data", JobCapability.MARKET_DATA), handler)
    brain.register(JobSpec("quant", JobCapability.QUANT_RESEARCH), handler)
    await brain.enqueue(
        "market-data",
        idempotency_key="shared-key",
        mode="LIVE_MONITORING",
    )

    with pytest.raises(ValueError, match="different job contract"):
        await brain.enqueue(
            "quant",
            idempotency_key="shared-key",
            mode="LIVE_MONITORING",
        )


@pytest.mark.asyncio
async def test_successful_job_runs_once(store: SqlJobStore) -> None:
    calls = 0

    async def handler(payload: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"processed": payload["symbol"]}

    brain = ProtoBrain(store=store, worker_id="worker-1")
    brain.register(JobSpec("quant", JobCapability.QUANT_RESEARCH), handler)
    queued = await brain.enqueue(
        "quant",
        idempotency_key="quant:btc:1",
        mode="SIMULATION",
        payload={"symbol": "BTC"},
    )

    completed = await brain.run_once()
    assert completed is not None
    assert completed.id == queued.id
    assert completed.state is JobState.SUCCEEDED
    assert completed.result == {"processed": "BTC"}
    assert calls == 1
    assert await brain.run_once() is None


@pytest.mark.asyncio
async def test_retry_uses_exponential_backoff_then_dead_letters(store: SqlJobStore) -> None:
    async def handler(_: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("boom")

    spec = JobSpec(
        "calibration",
        JobCapability.CALIBRATION,
        max_attempts=2,
        base_backoff_seconds=2,
    )
    brain = ProtoBrain(store=store, worker_id="worker-1")
    brain.register(spec, handler)
    await brain.enqueue(
        "calibration",
        idempotency_key="cal:1",
        mode="HISTORICAL_REPLAY",
    )
    t0 = datetime.now(UTC) + timedelta(seconds=1)

    failed_once = await brain.run_once(now=t0)
    assert failed_once is not None
    assert failed_once.state is JobState.RETRY_WAIT
    assert failed_once.attempts == 1
    assert failed_once.not_before == t0 + timedelta(seconds=2)

    assert await brain.run_once(now=t0 + timedelta(seconds=1)) is None
    failed_twice = await brain.run_once(now=t0 + timedelta(seconds=2))
    assert failed_twice is not None
    assert failed_twice.state is JobState.DEAD_LETTER
    assert failed_twice.attempts == 2
    assert len(await store.list_dead_letters()) == 1


@pytest.mark.asyncio
async def test_risk_gated_job_fails_closed_without_gate(store: SqlJobStore) -> None:
    async def handler(_: dict[str, object]) -> dict[str, object]:
        pytest.fail("handler must not run before independent risk approval")

    brain = ProtoBrain(store=store, worker_id="worker-1")
    brain.register(
        JobSpec(
            "paper-decision",
            JobCapability.PAPER_EXECUTION,
            requires_risk_gate=True,
            allowed_modes=frozenset({"PAPER_TRADING"}),
        ),
        handler,
    )
    await brain.enqueue(
        "paper-decision",
        idempotency_key="paper:1",
        mode="PAPER_TRADING",
    )

    blocked = await brain.run_once()
    assert blocked is not None
    assert blocked.state is JobState.BLOCKED
    assert blocked.last_error == "risk gate unavailable"


@pytest.mark.asyncio
async def test_heartbeat_renews_worker_lease(store: SqlJobStore) -> None:
    async def handler(payload: dict[str, object]) -> dict[str, object]:
        return payload

    spec = JobSpec("observability", JobCapability.OBSERVABILITY, lease_seconds=10)
    brain = ProtoBrain(store=store, worker_id="worker-a")
    brain.register(spec, handler)
    await brain.enqueue(
        "observability",
        idempotency_key="obs:1",
        mode="LIVE_MONITORING",
    )
    t0 = datetime.now(UTC) + timedelta(seconds=1)
    claimed = await store.claim_next(brain.specs, "worker-a", now=t0)
    assert claimed is not None
    assert claimed.lease_expires_at == t0 + timedelta(seconds=10)

    heartbeat = await brain.heartbeat(claimed.id, now=t0 + timedelta(seconds=5))
    assert heartbeat.heartbeat_at == t0 + timedelta(seconds=5)
    assert heartbeat.lease_expires_at == t0 + timedelta(seconds=15)


@pytest.mark.asyncio
async def test_expired_worker_lease_is_recovered(store: SqlJobStore) -> None:
    async def handler(payload: dict[str, object]) -> dict[str, object]:
        return payload

    spec = JobSpec(
        "replay",
        JobCapability.REPLAY,
        base_backoff_seconds=0,
        lease_seconds=1,
    )
    brain = ProtoBrain(store=store, worker_id="worker-a")
    brain.register(spec, handler)
    queued = await brain.enqueue(
        "replay",
        idempotency_key="replay:1",
        mode="HISTORICAL_REPLAY",
    )

    t0 = datetime.now(UTC) + timedelta(seconds=1)
    claimed = await store.claim_next(brain.specs, "dead-worker", now=t0)
    assert claimed is not None
    assert claimed.state is JobState.RUNNING

    completed = await brain.run_once(now=t0 + timedelta(seconds=2))
    assert completed is not None
    assert completed.id == queued.id
    assert completed.state is JobState.SUCCEEDED
    assert completed.attempts == 2


def test_real_financial_side_effects_are_rejected_by_contract() -> None:
    with pytest.raises(ValueError, match="real financial side effects"):
        JobSpec(
            "forbidden-live-order",
            JobCapability.PAPER_EXECUTION,
            financial_side_effects=True,
        )
