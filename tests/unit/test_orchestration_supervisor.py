from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.app.orchestration_state import _market_data_health_job
from services.orchestration import JobCapability, JobSpec, ProtoBrain, SqlJobStore
from services.orchestration.supervisor import OrchestrationSupervisor, PeriodicJob


@pytest.mark.asyncio
async def test_periodic_tick_is_idempotent_within_schedule_bucket() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    store = SqlJobStore(engine)
    await store.init_schema()
    calls = 0

    async def handler(payload: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"scheduled_at": payload["scheduled_at"]}

    brain = ProtoBrain(store=store, worker_id="test-supervisor")
    brain.register(
        JobSpec(
            "health",
            JobCapability.OBSERVABILITY,
            allowed_modes=frozenset({"LIVE_MONITORING"}),
        ),
        handler,
    )
    supervisor = OrchestrationSupervisor(
        brain,
        (PeriodicJob("health", 10, "LIVE_MONITORING"),),
        poll_seconds=0.01,
    )
    now = datetime(2026, 9, 2, 23, 40, 5, tzinfo=UTC)

    await supervisor.tick(now=now)
    await supervisor.tick(now=now)

    assert calls == 1
    assert supervisor.status()["ticks"] == 2
    assert supervisor.status()["real_money_execution"] is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_real_market_health_job_preserves_read_only_provenance() -> None:
    result = await _market_data_health_job({})
    assert result["financial_connectivity"] is False
    assert result["real_money_execution"] is False
    assert result["source"] == "PUBLIC_READ_ONLY"
    assert result["provider"] in {"COINBASE", "BINANCE", "CUSTOM"}
