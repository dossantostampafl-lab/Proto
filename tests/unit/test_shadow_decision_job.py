from datetime import UTC, datetime
from uuid import uuid4

import pytest

from apps.api.app import orchestration_state as module
from apps.api.app.orchestration_state import _shadow_decision_job
from services.orchestration import DecisionStage


class MemoryProbe:
    def __init__(self) -> None:
        self.entries = []

    async def record(self, entry):
        self.entries.append(entry)
        return entry


@pytest.mark.asyncio
async def test_shadow_decision_persists_fact_only_lineage_and_never_executes(monkeypatch) -> None:
    memory = MemoryProbe()
    monkeypatch.setattr(module, "decision_memory_store", memory)
    decision_id = uuid4()

    result = await _shadow_decision_job(
        {
            "decision_id": decision_id,
            "instrument_id": "US:AAPL",
            "observed_at": datetime(2026, 9, 3, 1, 30, tzinfo=UTC),
            "input_hash": "0123456789abcdef0123456789abcdef",
            "proposed_action": "WATCH_LONG",
            "provenance": {"source": "ALPACA_READ_ONLY"},
        }
    )

    assert len(memory.entries) == 1
    entry = memory.entries[0]
    assert entry.decision_id == decision_id
    assert entry.instrument_id == "US:AAPL"
    assert entry.stage == DecisionStage.SHADOW_ONLY
    assert entry.proposed_action == "WATCH_LONG"
    assert entry.actual_action is None
    assert entry.model_id is None
    assert entry.probability is None
    assert entry.edge is None
    assert result["stage"] == "SHADOW_ONLY"
    assert result["executed"] is False
    assert result["memory_persisted"] is True
    assert result["financial_connectivity"] is False
    assert result["real_money_execution"] is False


@pytest.mark.asyncio
async def test_shadow_decision_requires_persistence(monkeypatch) -> None:
    monkeypatch.setattr(module, "decision_memory_store", None)
    with pytest.raises(RuntimeError, match="persistence is unavailable"):
        await _shadow_decision_job({})


@pytest.mark.asyncio
async def test_shadow_decision_rejects_missing_fact_fields(monkeypatch) -> None:
    monkeypatch.setattr(module, "decision_memory_store", MemoryProbe())
    with pytest.raises(ValueError, match="missing required fields"):
        await _shadow_decision_job(
            {
                "decision_id": uuid4(),
                "instrument_id": "CRYPTO:BTC",
                "observed_at": datetime(2026, 9, 3, 1, 30, tzinfo=UTC),
                "input_hash": "0123456789abcdef0123456789abcdef",
                "proposed_action": "WATCH_LONG",
            }
        )


@pytest.mark.asyncio
async def test_shadow_payload_cannot_force_actual_execution(monkeypatch) -> None:
    memory = MemoryProbe()
    monkeypatch.setattr(module, "decision_memory_store", memory)

    result = await _shadow_decision_job(
        {
            "decision_id": uuid4(),
            "instrument_id": "CRYPTO:ETH",
            "observed_at": datetime(2026, 9, 3, 1, 30, tzinfo=UTC),
            "input_hash": "abcdef0123456789abcdef0123456789",
            "proposed_action": "WATCH_SHORT",
            "actual_action": "SELL",
            "stage": "PAPER_EXECUTED",
            "provenance": {"source": "PUBLIC_READ_ONLY"},
        }
    )

    assert memory.entries[0].stage == DecisionStage.SHADOW_ONLY
    assert memory.entries[0].actual_action is None
    assert result["executed"] is False
