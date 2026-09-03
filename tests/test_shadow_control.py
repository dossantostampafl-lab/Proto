from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from apps.api.app.app_state import portfolio, reset_runtime_state, runtime
from apps.api.app.models import Asset, MarketSnapshot, Side, SimulationOrder, SimulationRequest, SystemMode
from apps.api.app.shadow_control import (
    evaluate_shadow_decision,
    shadow_status,
    start_shadow_mode,
    stop_shadow_mode,
)


def _request(*, quantity: float = 0.01, ask_size: float = 1.0) -> SimulationRequest:
    return SimulationRequest(
        order=SimulationOrder(
            market_id="BTC-shadow",
            asset=Asset.BTC,
            side=Side.BUY,
            quantity=quantity,
            limit_price=101.0,
        ),
        snapshot=MarketSnapshot(
            symbol="BTC",
            market_id="BTC-shadow",
            bid=100.0,
            ask=100.1,
            bid_size=1.0,
            ask_size=ask_size,
            volatility=0.2,
            imbalance=0.4,
            market_probability=0.55,
            observed_at=datetime.now(UTC),
        ),
    )


def setup_function() -> None:
    reset_runtime_state()
    portfolio.reset()


def teardown_function() -> None:
    reset_runtime_state()
    portfolio.reset()


def test_shadow_start_and_stop_are_explicit_runtime_transitions() -> None:
    asyncio.run(start_shadow_mode())
    assert runtime.mode == SystemMode.SHADOW
    assert runtime.running is True

    status = shadow_status()
    assert status["shadow_evaluation_enabled"] is True
    assert status["portfolio_mutation"] is False
    assert status["fill_persistence"] is False
    assert status["financial_connectivity"] is False
    assert status["real_money_execution"] is False

    asyncio.run(stop_shadow_mode())
    assert runtime.mode == SystemMode.SHADOW
    assert runtime.running is False


def test_shadow_evaluation_returns_hypothetical_fill_without_portfolio_mutation() -> None:
    asyncio.run(start_shadow_mode())
    before_positions = portfolio.snapshot()["positions"]
    before_journal = portfolio.journal()

    result = evaluate_shadow_decision(_request())

    assert result["decision"]["mode"] == "SHADOW"
    assert result["decision"]["accepted"] is True
    assert result["decision"]["fill"] is not None
    assert result["would_execute"] is True
    assert result["portfolio_mutated"] is False
    assert result["fill_persisted"] is False
    assert portfolio.snapshot()["positions"] == before_positions
    assert portfolio.journal() == before_journal


def test_shadow_evaluation_preserves_authoritative_liquidity_rejection() -> None:
    asyncio.run(start_shadow_mode())

    result = evaluate_shadow_decision(_request(quantity=2.0, ask_size=0.01))

    assert result["decision"]["mode"] == "SHADOW"
    assert result["decision"]["accepted"] is False
    assert result["decision"]["reason"] == "insufficient top-of-book liquidity"
    assert result["would_execute"] is False
    assert portfolio.journal() == []


def test_shadow_evaluation_fails_closed_when_runtime_is_not_shadow() -> None:
    result = evaluate_shadow_decision(_request())

    assert result["decision"]["mode"] == "SHADOW"
    assert result["decision"]["accepted"] is False
    assert result["decision"]["reason"] == "shadow mode halted"
    assert result["would_execute"] is False
