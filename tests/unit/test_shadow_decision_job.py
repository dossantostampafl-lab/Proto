from datetime import UTC, datetime
from uuid import uuid4

import pytest

from apps.api.app import orchestration_state as module
from apps.api.app.app_state import portfolio, reset_runtime_state
from apps.api.app.orchestration_state import _shadow_decision_job
from apps.api.app.shadow_control import start_shadow_mode
from services.orchestration import DecisionStage


class MemoryProbe:
    def __init__(self) -> None:
        self.entries = []

    async def record(self, entry):
        self.entries.append(entry)
        return entry


def _simulation_request(
    *,
    asset: str = "BTC",
    side: str = "BUY",
    quantity: float = 0.01,
    bid: float = 100.0,
    ask: float = 100.1,
    bid_size: float = 1.0,
    ask_size: float = 1.0,
) -> dict[str, object]:
    observed_at = datetime.now(UTC)
    return {
        "order": {
            "market_id": f"{asset}-shadow",
            "asset": asset,
            "side": side,
            "quantity": quantity,
            "limit_price": ask if side == "BUY" else bid,
        },
        "snapshot": {
            "symbol": asset,
            "market_id": f"{asset}-shadow",
            "bid": bid,
            "ask": ask,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "volatility": 0.2,
            "imbalance": 0.4,
            "market_probability": 0.55,
            "observed_at": observed_at.isoformat(),
        },
    }


def setup_function() -> None:
    reset_runtime_state()
    portfolio.reset()


def teardown_function() -> None:
    reset_runtime_state()
    portfolio.reset()


@pytest.mark.asyncio
async def test_shadow_decision_persists_fact_only_lineage_and_never_executes(monkeypatch) -> None:
    memory = MemoryProbe()
    monkeypatch.setattr(module, "decision_memory_store", memory)
    decision_id = uuid4()
    await start_shadow_mode()
    before_positions = portfolio.snapshot()["positions"]

    result = await _shadow_decision_job(
        {
            "decision_id": decision_id,
            "instrument_id": "CRYPTO:BTC",
            "observed_at": datetime.now(UTC),
            "input_hash": "0123456789abcdef0123456789abcdef",
            "proposed_action": "BUY",
            "provenance": {"source": "PUBLIC_READ_ONLY"},
            "simulation_request": _simulation_request(),
        }
    )

    assert len(memory.entries) == 1
    entry = memory.entries[0]
    assert entry.decision_id == decision_id
    assert entry.instrument_id == "CRYPTO:BTC"
    assert entry.stage == DecisionStage.SHADOW_ONLY
    assert entry.proposed_action == "BUY"
    assert entry.actual_action is None
    assert entry.model_id is None
    assert entry.probability is None
    assert entry.edge is None
    assert entry.risk_decision == "APPROVED"
    assert result["stage"] == "SHADOW_ONLY"
    assert result["shadow_result"]["accepted"] is True
    assert result["would_execute"] is True
    assert result["executed"] is False
    assert result["portfolio_mutated"] is False
    assert result["fill_persisted"] is False
    assert result["memory_persisted"] is True
    assert result["financial_connectivity"] is False
    assert result["real_money_execution"] is False
    assert portfolio.snapshot()["positions"] == before_positions
    assert portfolio.journal() == []


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
                "observed_at": datetime.now(UTC),
                "input_hash": "0123456789abcdef0123456789abcdef",
                "proposed_action": "WATCH_LONG",
            }
        )


@pytest.mark.asyncio
async def test_shadow_payload_cannot_force_actual_execution(monkeypatch) -> None:
    memory = MemoryProbe()
    monkeypatch.setattr(module, "decision_memory_store", memory)
    await start_shadow_mode()

    result = await _shadow_decision_job(
        {
            "decision_id": uuid4(),
            "instrument_id": "CRYPTO:ETH",
            "observed_at": datetime.now(UTC),
            "input_hash": "abcdef0123456789abcdef0123456789",
            "proposed_action": "SELL",
            "actual_action": "SELL",
            "stage": "PAPER_EXECUTED",
            "provenance": {"source": "PUBLIC_READ_ONLY"},
            "simulation_request": _simulation_request(asset="ETH", side="SELL"),
        }
    )

    assert memory.entries[0].stage == DecisionStage.SHADOW_ONLY
    assert memory.entries[0].actual_action is None
    assert result["executed"] is False
    assert result["portfolio_mutated"] is False
    assert result["fill_persisted"] is False
    assert result["real_money_execution"] is False
    assert portfolio.journal() == []
