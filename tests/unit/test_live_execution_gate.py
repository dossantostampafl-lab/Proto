from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from services.live_execution import (
    ExecutionMode,
    LiveExecutionConfig,
    LiveExecutionGate,
    OrderIntent,
    RiskDecision,
    TradeIntent,
)

NOW = datetime.now(UTC)


def _trade() -> TradeIntent:
    return TradeIntent(
        intent_id="trade-0001",
        correlation_id="corr-0001",
        strategy_id="strategy-alpha",
        instrument="BTC-USD",
        side="BUY",
        proposed_quantity=0.001,
        reference_price=100_000.0,
        valid_until=NOW + timedelta(minutes=5),
        rationale="deterministic test intent",
        model_version="model-v1",
        signature="signed-intent-placeholder",
    )


def _risk(*, approved: bool = True) -> RiskDecision:
    return RiskDecision(
        decision_id="risk-0001",
        correlation_id="corr-0001",
        approved=approved,
        reason="approved for deterministic test" if approved else "rejected",
        account_state_confirmed=approved,
        margin_confirmed=approved,
        position_state_confirmed=approved,
        market_data_fresh=approved,
        reconciliation_clean=approved,
        kill_switch_armed=False,
        requested_notional=100.0 if approved else 0.0,
        approved_notional_limit=100.0 if approved else 0.0,
        margin_snapshot_id="margin-0001" if approved else None,
    )


def _order() -> OrderIntent:
    return OrderIntent(
        order_intent_id="order-0001",
        correlation_id="corr-0001",
        idempotency_key="idempotency-key-0001",
        instrument="BTC-USD",
        side="BUY",
        order_type="LIMIT",
        quantity=0.001,
        limit_price=100_000.0,
        time_in_force="IOC",
        expires_at=NOW + timedelta(minutes=2),
    )


def _fully_authorized_config() -> LiveExecutionConfig:
    return LiveExecutionConfig(
        mode=ExecutionMode.LIVE_CANARY,
        live_execution_enabled=True,
        custody_enabled=False,
        withdrawals_enabled=False,
        human_activation_required=True,
        human_activation_recorded=True,
        adapter_validated=True,
        permissions_validated=True,
        canary_max_notional=100.0,
        live_max_notional=1_000.0,
    )


def test_defaults_are_fail_closed() -> None:
    decision = LiveExecutionGate(LiveExecutionConfig()).evaluate(
        trade_intent=_trade(),
        risk_decision=_risk(),
        order_intent=_order(),
    )
    assert decision.allowed is False
    assert decision.mode == ExecutionMode.SIMULATION


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("live_execution_enabled", False),
        ("human_activation_required", False),
        ("human_activation_recorded", False),
        ("adapter_validated", False),
        ("permissions_validated", False),
        ("canary_max_notional", 0.0),
    ],
)
def test_each_required_live_gate_fails_closed(field: str, value: object) -> None:
    config = _fully_authorized_config().model_copy(update={field: value})
    decision = LiveExecutionGate(config).evaluate(
        trade_intent=_trade(),
        risk_decision=_risk(),
        order_intent=_order(),
    )
    assert decision.allowed is False


def test_custody_or_withdrawals_force_rejection() -> None:
    for field in ("custody_enabled", "withdrawals_enabled"):
        config = _fully_authorized_config().model_copy(update={field: True})
        decision = LiveExecutionGate(config).evaluate(
            trade_intent=_trade(),
            risk_decision=_risk(),
            order_intent=_order(),
        )
        assert decision.allowed is False


def test_approved_risk_decision_requires_confirmed_margin_and_state() -> None:
    with pytest.raises(ValidationError):
        RiskDecision(
            decision_id="risk-0002",
            correlation_id="corr-0001",
            approved=True,
            reason="invalid approval",
            account_state_confirmed=True,
            margin_confirmed=False,
            position_state_confirmed=True,
            market_data_fresh=True,
            reconciliation_clean=True,
            kill_switch_armed=False,
            requested_notional=10.0,
            approved_notional_limit=20.0,
            margin_snapshot_id=None,
        )


def test_duplicate_idempotency_key_is_not_resent() -> None:
    gate = LiveExecutionGate(_fully_authorized_config())
    first = gate.evaluate(
        trade_intent=_trade(),
        risk_decision=_risk(),
        order_intent=_order(),
    )
    second = gate.evaluate(
        trade_intent=_trade(),
        risk_decision=_risk(),
        order_intent=_order(),
    )
    assert first.allowed is True
    assert second.allowed is False
    assert "duplicate idempotency key" in second.reason


def test_order_above_canary_limit_is_rejected() -> None:
    config = _fully_authorized_config().model_copy(update={"canary_max_notional": 50.0})
    decision = LiveExecutionGate(config).evaluate(
        trade_intent=_trade(),
        risk_decision=_risk(),
        order_intent=_order(),
    )
    assert decision.allowed is False
    assert "configured live notional limit" in decision.reason
