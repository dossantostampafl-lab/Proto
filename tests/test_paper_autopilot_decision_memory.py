from datetime import UTC, datetime

import pytest

from apps.api.app import paper_autopilot as module
from apps.api.app.models import SimulationResult, SystemMode
from apps.api.app.paper_autopilot import PaperAutopilotConfig, PaperAutopilotService
from services.orchestration import DecisionStage


class MemoryProbe:
    def __init__(self) -> None:
        self.entries = []

    async def record(self, entry):
        self.entries.append(entry)
        return entry


@pytest.mark.asyncio
async def test_autopilot_records_only_observed_decision_facts(monkeypatch) -> None:
    service = PaperAutopilotService()
    memory = MemoryProbe()
    monkeypatch.setattr(module, "decision_memory_store", memory)
    monkeypatch.setattr(service, "_live_market_ready", lambda _symbol: True)

    async def fake_simulate(_request):
        return SimulationResult(
            mode=SystemMode.PAPER_TRADING,
            accepted=False,
            reason="risk limit",
            fill=None,
        )

    monkeypatch.setattr(module, "simulate", fake_simulate)
    config = PaperAutopilotConfig(
        symbol="BTC",
        imbalance_trigger=0.65,
        cooldown_seconds=20,
        quantity=0.001,
        max_spread_bps=20,
        stop_loss_fraction=0.05,
    )

    accepted = await service._submit(
        config=config,
        side="BUY",
        quantity=0.001,
        bid=100.0,
        ask=100.1,
        bid_size=1.0,
        ask_size=1.0,
        volatility=0.2,
        imbalance=0.8,
        observed_at=datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
        reason="SIMULATED_FILL",
    )

    assert accepted is False
    assert len(memory.entries) == 1
    entry = memory.entries[0]
    assert entry.instrument_id == "CRYPTO:BTC"
    assert entry.stage == DecisionStage.RISK_REJECTED
    assert entry.risk_decision == "risk limit"
    assert entry.proposed_action == "BUY"
    assert entry.actual_action is None
    assert entry.probability is None
    assert entry.edge is None
    assert entry.model_id is None
    assert entry.provenance["decision_source"] == "PAPER_AUTOPILOT"
    assert entry.provenance["market_data_source"] == "PUBLIC_READ_ONLY"
    assert len(entry.input_hash) == 64
    assert service.status()["last_decision_id"] == str(entry.decision_id)


@pytest.mark.asyncio
async def test_memory_failure_is_observable_without_rewriting_execution_result(monkeypatch) -> None:
    service = PaperAutopilotService()

    class FailingMemory:
        async def record(self, _entry):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(module, "decision_memory_store", FailingMemory())
    monkeypatch.setattr(service, "_live_market_ready", lambda _symbol: True)

    async def fake_simulate(_request):
        return SimulationResult(
            mode=SystemMode.PAPER_TRADING,
            accepted=True,
            reason="approved",
            fill=None,
        )

    monkeypatch.setattr(module, "simulate", fake_simulate)
    config = PaperAutopilotConfig(stop_loss_fraction=0.05)

    accepted = await service._submit(
        config=config,
        side="BUY",
        quantity=0.001,
        bid=100.0,
        ask=100.1,
        bid_size=1.0,
        ask_size=1.0,
        volatility=0.2,
        imbalance=0.8,
        observed_at=datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
        reason="SIMULATED_FILL",
    )

    assert accepted is True
    status = service.status()
    assert status["counters"]["decision_memory_failures"] == 1
    assert status["last_reason"] == "SIMULATED_FILL"
    assert status["last_decision_id"] is None
