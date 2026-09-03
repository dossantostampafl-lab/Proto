from datetime import UTC, datetime
from uuid import uuid4

import pytest

from apps.api.app import orchestration_surface as module
from services.orchestration import DecisionMemoryEntry, DecisionOutcome, DecisionStage


class MemoryProbe:
    def __init__(self, rows=None) -> None:
        self.rows = rows or []
        self.recent_args = None

    async def snapshot(self):
        return {
            "records": len(self.rows),
            "resolved": sum(outcome is not None for _, outcome in self.rows),
            "unresolved": sum(outcome is None for _, outcome in self.rows),
            "financial_connectivity": False,
            "real_money_execution": False,
        }

    async def recent(self, *, instrument_id=None, limit=100):
        self.recent_args = (instrument_id, limit)
        return self.rows[:limit]


def _row():
    decision_id = uuid4()
    observed_at = datetime(2026, 9, 3, 2, 0, tzinfo=UTC)
    entry = DecisionMemoryEntry(
        decision_id=decision_id,
        instrument_id="CRYPTO:BTC",
        observed_at=observed_at,
        recorded_at=observed_at,
        stage=DecisionStage.SHADOW_ONLY,
        input_hash="0123456789abcdef0123456789abcdef",
        proposed_action="WATCH_LONG",
        provenance={"source": "PUBLIC_READ_ONLY"},
    )
    outcome = DecisionOutcome(
        decision_id=decision_id,
        resolved_at=observed_at,
        outcome="observed",
    )
    return entry, outcome


@pytest.mark.asyncio
async def test_decision_memory_status_reports_persisted_counts(monkeypatch) -> None:
    memory = MemoryProbe([_row()])
    monkeypatch.setattr(module, "decision_memory_store", memory)

    result = await module.decision_memory_status()

    assert result["configured"] is True
    assert result["records"] == 1
    assert result["resolved"] == 1
    assert result["unresolved"] == 0
    assert result["financial_connectivity"] is False
    assert result["real_money_execution"] is False


@pytest.mark.asyncio
async def test_recent_decisions_preserves_fact_only_lineage(monkeypatch) -> None:
    row = _row()
    memory = MemoryProbe([row])
    monkeypatch.setattr(module, "decision_memory_store", memory)

    result = await module.recent_decisions(instrument_id="crypto:btc", limit=5)

    assert memory.recent_args == ("crypto:btc", 5)
    assert result["configured"] is True
    assert result["instrument_id"] == "CRYPTO:BTC"
    assert result["count"] == 1
    decision = result["decisions"][0]["decision"]
    assert decision["stage"] == "SHADOW_ONLY"
    assert decision["actual_action"] is None
    assert decision["probability"] is None
    assert decision["edge"] is None
    assert result["decisions"][0]["outcome"]["outcome"] == "observed"
    assert result["financial_connectivity"] is False
    assert result["real_money_execution"] is False


@pytest.mark.asyncio
async def test_unconfigured_memory_is_explicitly_empty(monkeypatch) -> None:
    monkeypatch.setattr(module, "decision_memory_store", None)

    status = await module.decision_memory_status()
    recent = await module.recent_decisions(instrument_id=None, limit=100)

    assert status["configured"] is False
    assert status["records"] == 0
    assert recent["configured"] is False
    assert recent["decisions"] == []
    assert recent["count"] == 0
