from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.app import event_state
from apps.api.app.event_surface import router as event_router
from services.events import SqlJournalStore
from services.events.journal_store import journal_table


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
    store = SqlJournalStore(engine)
    await store.create_schema()
    journal = event_state.OperationalJournal(store)
    await journal.start()
    monkeypatch.setattr(event_state, "operational_journal", journal)

    recorded = await event_state.record_operational_event(
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
        row = (await connection.execute(select(journal_table))).one()

    assert recorded.created is True
    assert snapshot["count"] == 1
    assert snapshot["chain_valid"] is True
    assert snapshot["integrity"] == "VALID"
    assert snapshot["write_failures"] == 0
    assert row.event_id == recorded.event["event_id"]
    assert row.event_type == "RISK_REJECTED"
    assert row.hash == recorded.event["hash"]
    assert '"reason":"stale market snapshot"' in row.payload_json

    await engine.dispose()
