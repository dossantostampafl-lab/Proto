from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from services.live_execution import (
    AccountRiskSnapshot,
    DeterministicPreTradeRiskEngine,
    NormalizedAccountState,
    OrderIntent,
    OrderManagementSystem,
    OrderState,
    ReconciliationEngine,
    RiskLimits,
    SignalRiskSnapshot,
    TradeIntent,
)

NOW = datetime.now(UTC)


def _trade() -> TradeIntent:
    return TradeIntent(
        intent_id="trade-1001",
        correlation_id="corr-1001",
        strategy_id="strategy-alpha",
        instrument="BTC-USD",
        side="BUY",
        proposed_quantity=0.001,
        reference_price=100_000.0,
        valid_until=NOW + timedelta(minutes=5),
        rationale="deterministic control test",
        model_version="model-v1",
        signature="signed-intent-placeholder",
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        max_order_notional=200.0,
        max_daily_loss=500.0,
        max_drawdown=500.0,
        max_data_age_ms=1_000.0,
        max_spread_bps=10.0,
        max_slippage_bps=15.0,
        minimum_confidence=0.70,
        maximum_uncertainty=0.30,
        minimum_probability=0.55,
        minimum_net_edge=0.01,
        minimum_expected_value=0.0,
    )


def _account() -> AccountRiskSnapshot:
    return AccountRiskSnapshot(
        account_state_confirmed=True,
        margin_confirmed=True,
        position_state_confirmed=True,
        market_data_fresh=True,
        reconciliation_clean=True,
        kill_switch_armed=False,
        buying_power=1_000.0,
        margin_available=1_000.0,
        margin_snapshot_id="margin-1001",
        market_data_age_ms=100.0,
        daily_loss=0.0,
        drawdown=0.0,
        spread_bps=2.0,
        estimated_slippage_bps=3.0,
    )


def _signal() -> SignalRiskSnapshot:
    return SignalRiskSnapshot(
        confidence=0.90,
        uncertainty=0.10,
        probability=0.65,
        net_edge=0.03,
        expected_value=0.02,
        execution_costs_complete=True,
    )


def _order() -> OrderIntent:
    return OrderIntent(
        order_intent_id="order-1001",
        correlation_id="corr-1001",
        idempotency_key="idempotency-key-1001",
        instrument="BTC-USD",
        side="BUY",
        order_type="LIMIT",
        quantity=0.001,
        limit_price=100_000.0,
        time_in_force="IOC",
        expires_at=NOW + timedelta(minutes=2),
    )


def test_risk_engine_approves_only_complete_known_state() -> None:
    decision = DeterministicPreTradeRiskEngine(_limits()).evaluate(
        trade_intent=_trade(),
        account=_account(),
        signal=_signal(),
    )
    assert decision.approved is True
    assert decision.reason == "APPROVED"
    assert decision.margin_snapshot_id == "margin-1001"


@pytest.mark.parametrize(
    ("account_update", "signal_update", "reason"),
    [
        ({"kill_switch_armed": True}, {}, "KILL_SWITCH_ARMED"),
        ({"margin_confirmed": False}, {}, "NO_CONFIRMED_MARGIN"),
        ({"position_state_confirmed": False}, {}, "UNKNOWN_POSITION"),
        ({"reconciliation_clean": False}, {}, "RECONCILIATION_FAILURE"),
        ({"market_data_fresh": False}, {}, "NO_FRESH_MARKET_DATA"),
        ({"market_data_age_ms": 2_000.0}, {}, "STALE_MARKET_DATA"),
        ({}, {"execution_costs_complete": False}, "EXECUTION_COSTS_UNKNOWN"),
        ({}, {"net_edge": None}, "NET_EDGE_UNKNOWN"),
        ({}, {"expected_value": None}, "EXPECTED_VALUE_UNKNOWN"),
        ({"spread_bps": 20.0}, {}, "SPREAD_LIMIT"),
        ({"estimated_slippage_bps": 20.0}, {}, "SLIPPAGE_LIMIT"),
    ],
)
def test_risk_engine_fails_closed(
    account_update: dict[str, object],
    signal_update: dict[str, object],
    reason: str,
) -> None:
    account = _account().model_copy(update=account_update)
    signal = _signal().model_copy(update=signal_update)
    decision = DeterministicPreTradeRiskEngine(_limits()).evaluate(
        trade_intent=_trade(),
        account=account,
        signal=signal,
    )
    assert decision.approved is False
    assert decision.reason == reason


def test_oms_enforces_idempotency_and_state_machine() -> None:
    oms = OrderManagementSystem()
    registered = oms.register(_order())
    assert registered.state == OrderState.CREATED

    pending = oms.transition("order-1001", OrderState.SUBMIT_PENDING)
    assert pending.state == OrderState.SUBMIT_PENDING

    acknowledged = oms.transition(
        "order-1001",
        OrderState.ACKNOWLEDGED,
        external_order_id="external-1001",
    )
    assert acknowledged.external_order_id == "external-1001"

    partial = oms.transition(
        "order-1001",
        OrderState.PARTIALLY_FILLED,
        filled_quantity=0.0005,
    )
    assert partial.filled_quantity == 0.0005

    filled = oms.transition(
        "order-1001",
        OrderState.FILLED,
        filled_quantity=0.001,
    )
    assert filled.state == OrderState.FILLED

    with pytest.raises(ValueError, match="duplicate idempotency key"):
        oms.register(_order().model_copy(update={"order_intent_id": "order-1002"}))
    with pytest.raises(ValueError, match="invalid order transition"):
        oms.transition("order-1001", OrderState.CANCELLED)


def test_oms_rejects_ambiguous_complete_fill() -> None:
    oms = OrderManagementSystem()
    oms.register(_order())
    oms.transition("order-1001", OrderState.SUBMIT_PENDING)
    oms.transition("order-1001", OrderState.ACKNOWLEDGED)
    with pytest.raises(ValueError, match="FILLED requires complete filled quantity"):
        oms.transition("order-1001", OrderState.FILLED, filled_quantity=0.0005)


def _account_state(*, balance: float = 1_000.0) -> NormalizedAccountState:
    return NormalizedAccountState(
        snapshot_id="snapshot-1001",
        balance=balance,
        margin_available=900.0,
        positions={"BTC-USD": 0.001},
        open_orders={"external-1001"},
        observed_at=NOW,
    )


def test_reconciliation_clean_when_states_match() -> None:
    result = ReconciliationEngine().reconcile(
        correlation_id="corr-1001",
        internal=_account_state(),
        source_of_truth=_account_state(),
    )
    assert result.clean is True
    assert result.halt_required is False
    assert result.events == []


def test_reconciliation_divergence_requires_halt() -> None:
    result = ReconciliationEngine().reconcile(
        correlation_id="corr-1001",
        internal=_account_state(),
        source_of_truth=_account_state(balance=999.0),
    )
    assert result.clean is False
    assert result.halt_required is True
    assert result.events[0].component == "balance"
    assert result.events[0].severity == "CRITICAL"
