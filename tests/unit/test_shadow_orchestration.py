from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from apps.api.app import orchestration_state
from apps.api.app.app_state import portfolio, reset_runtime_state
from apps.api.app.shadow_control import start_shadow_mode
from services.orchestration import DecisionMemoryEntry


class _MemoryStore:
    def __init__(self) -> None:
        self.entries: list[DecisionMemoryEntry] = []

    async def record(self, entry: DecisionMemoryEntry) -> DecisionMemoryEntry:
        self.entries.append(entry)
        return entry


def _payload(*, quantity: float = 0.01, ask_size: float = 1.0) -> dict[str, object]:
    observed_at = datetime.now(UTC)
    return {
        "decision_id": str(uuid4()),
        "instrument_id": "CRYPTO:BTC",
        "observed_at": observed_at.isoformat(),
        "input_hash": "0123456789abcdef0123456789abcdef",
        "proposed_action": "BUY",
        "provenance": {"source": "test-observation"},
        "simulation_request": {
            "order": {
                "market_id": "BTC-shadow",
                "asset": "BTC",
                "side": "BUY",
                "quantity": quantity,
                "limit_price": 101.0,
            },
            "snapshot": {
                "symbol": "BTC",
                "market_id": "BTC-shadow",
                "bid": 100.0,
                "ask": 100.1,
                "bid_size": 1.0,
                "ask_size": ask_size,
                "volatility": 0.2,
                "imbalance": 0.4,
                "market_probability": 0.55,
                "observed_at": observed_at.isoformat(),
            },
        },
    }


def setup_function() -> None:
    reset_runtime_state()
    portfolio.reset()


def teardown_function() -> None:
    reset_runtime_state()
    portfolio.reset()


@pytest.mark.asyncio
async def test_shadow_job_persists_authoritative_approval_without_portfolio_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = _MemoryStore()
    monkeypatch.setattr(orchestration_state, "decision_memory_store", memory)
    await start_shadow_mode()
    before = portfolio.snapshot()

    result = await orchestration_state._shadow_decision_job(_payload())

    assert result["shadow_result"]["accepted"] is True
    assert result["would_execute"] is True
    assert result["executed"] is False
    assert result["portfolio_mutated"] is False
    assert result["fill_persisted"] is False
    assert result["financial_connectivity"] is False
    assert result["real_money_execution"] is False
    assert portfolio.snapshot()["positions"] == before["positions"]
    assert portfolio.journal() == []
    assert len(memory.entries) == 1
    assert memory.entries[0].risk_decision == "APPROVED"
    assert memory.entries[0].actual_action is None


@pytest.mark.asyncio
async def test_shadow_job_persists_authoritative_rejection_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = _MemoryStore()
    monkeypatch.setattr(orchestration_state, "decision_memory_store", memory)
    await start_shadow_mode()

    result = await orchestration_state._shadow_decision_job(
        _payload(quantity=2.0, ask_size=0.01)
    )

    assert result["shadow_result"]["accepted"] is False
    assert result["would_execute"] is False
    assert result["executed"] is False
    assert result["portfolio_mutated"] is False
    assert result["fill_persisted"] is False
    assert memory.entries[0].risk_decision == "REJECTED:insufficient top-of-book liquidity"
    assert memory.entries[0].actual_action is None


def test_shadow_job_fails_closed_if_required_simulation_request_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = _MemoryStore()
    monkeypatch.setattr(orchestration_state, "decision_memory_store", memory)
    payload = _payload()
    payload.pop("simulation_request")

    with pytest.raises(ValueError, match="simulation_request"):
        asyncio.run(orchestration_state._shadow_decision_job(payload))
