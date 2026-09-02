from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.app import event_state
from apps.api.app.event_surface import router as event_router
from apps.api.app.schema_registry import CANONICAL_TABLES, canonical_metadata


def test_event_router_exposes_ready_hash_chain() -> None:
    app = FastAPI()
    app.include_router(event_router)

    with TestClient(app) as client:
        ready = client.get("/events/ready")
        journal = client.get("/events/journal/verify")

    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert journal.status_code == 200
    assert journal.json()["chain_valid"] is True


@pytest.mark.asyncio
async def test_operational_event_is_hash_chained_and_persisted(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    table = CANONICAL_TABLES["audit_events"]
    async with engine.begin() as connection:
        await connection.run_sync(canonical_metadata.create_all)

    monkeypatch.setattr(event_state, "persistence_engine", engine)
    before = event_state.operational_journal_snapshot(limit=1)["count"]

    event = await event_state.record_operational_event(
        event_type="RISK_REJECTED",
        source="test-risk",
        payload={
            "reason": "stale market snapshot",
            "financial_connectivity": False,
            "real_money_execution": False,
        },
    )

    snapshot = event_state.operational_journal_snapshot(limit=1)
    async with engine.connect() as connection:
        persisted = await connection.scalar(select(func.count()).select_from(table))
        row = (await connection.execute(select(table.c.payload))).scalar_one()

    assert snapshot["count"] == before + 1
    assert snapshot["chain_valid"] is True
    assert snapshot["persistence_failures"] == 0
    assert persisted == 1
    assert row["event_id"] == event["event_id"]
    assert row["event_type"] == "RISK_REJECTED"
    assert row["payload"]["reason"] == "stale market snapshot"

    await engine.dispose()
