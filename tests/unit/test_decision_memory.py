from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from services.orchestration.memory import (
    DecisionMemoryEntry,
    DecisionMemoryStore,
    DecisionOutcome,
    DecisionStage,
)


@pytest_asyncio.fixture
async def memory() -> DecisionMemoryStore:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    store = DecisionMemoryStore(engine)
    await store.init_schema()
    try:
        yield store
    finally:
        await engine.dispose()


def _entry() -> DecisionMemoryEntry:
    now = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)
    return DecisionMemoryEntry(
        decision_id=uuid4(),
        instrument_id="crypto:btc",
        observed_at=now,
        recorded_at=now + timedelta(milliseconds=10),
        stage=DecisionStage.PROPOSED,
        input_hash="0123456789abcdef",
        provenance={"source": "PUBLIC_READ_ONLY"},
    )


@pytest.mark.asyncio
async def test_decision_memory_preserves_missing_model_fields_as_null(
    memory: DecisionMemoryStore,
) -> None:
    entry = _entry()
    stored = await memory.record(entry)
    assert stored.instrument_id == "CRYPTO:BTC"
    assert stored.model_id is None
    assert stored.probability is None
    assert stored.edge is None

    fetched = await memory.get(entry.decision_id)
    assert fetched is not None
    assert fetched[0] == stored
    assert fetched[1] is None


@pytest.mark.asyncio
async def test_decision_record_is_idempotent_but_immutable(memory: DecisionMemoryStore) -> None:
    entry = _entry()
    first = await memory.record(entry)
    second = await memory.record(entry)
    assert first == second

    changed = entry.model_copy(update={"regime": "BULL"})
    with pytest.raises(ValueError, match="different decision record"):
        await memory.record(changed)


@pytest.mark.asyncio
async def test_outcome_is_immutable_and_cannot_predate_decision(
    memory: DecisionMemoryStore,
) -> None:
    entry = _entry()
    await memory.record(entry)

    too_early = DecisionOutcome(
        decision_id=entry.decision_id,
        resolved_at=entry.observed_at - timedelta(seconds=1),
        outcome="paper fill closed",
    )
    with pytest.raises(ValueError, match="cannot predate"):
        await memory.record_outcome(too_early)

    outcome = DecisionOutcome(
        decision_id=entry.decision_id,
        resolved_at=entry.observed_at + timedelta(minutes=5),
        outcome="paper fill closed",
        pnl=None,
    )
    assert await memory.record_outcome(outcome) == outcome
    assert await memory.record_outcome(outcome) == outcome

    changed = outcome.model_copy(update={"outcome": "different outcome"})
    with pytest.raises(ValueError, match="immutable"):
        await memory.record_outcome(changed)


@pytest.mark.asyncio
async def test_snapshot_never_claims_financial_connectivity(memory: DecisionMemoryStore) -> None:
    await memory.record(_entry())
    snapshot = await memory.snapshot()
    assert snapshot["records"] == 1
    assert snapshot["financial_connectivity"] is False
    assert snapshot["real_money_execution"] is False


def test_memory_contract_rejects_naive_time_and_unnamespaced_instrument() -> None:
    now = datetime(2026, 9, 3, 0, 0)
    with pytest.raises(ValidationError):
        DecisionMemoryEntry(
            decision_id=uuid4(),
            instrument_id="BTC",
            observed_at=now,
            recorded_at=now,
            stage=DecisionStage.PROPOSED,
            input_hash="0123456789abcdef",
        )
