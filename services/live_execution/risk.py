from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from pydantic import BaseModel, Field

from .contracts import RiskDecision, TradeIntent


class AccountRiskSnapshot(BaseModel):
    account_state_confirmed: bool = False
    margin_confirmed: bool = False
    position_state_confirmed: bool = False
    market_data_fresh: bool = False
    reconciliation_clean: bool = False
    kill_switch_armed: bool = True
    buying_power: float | None = Field(default=None, ge=0.0)
    margin_available: float | None = Field(default=None, ge=0.0)
    margin_snapshot_id: str | None = Field(default=None, max_length=256)
    market_data_age_ms: float | None = Field(default=None, ge=0.0)
    daily_loss: float = Field(default=0.0, ge=0.0)
    drawdown: float = Field(default=0.0, ge=0.0)
    spread_bps: float | None = Field(default=None, ge=0.0)
    estimated_slippage_bps: float | None = Field(default=None, ge=0.0)


class SignalRiskSnapshot(BaseModel):
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: float = Field(ge=0.0, le=1.0)
    probability: float = Field(ge=0.0, le=1.0)
    net_edge: float | None = None
    expected_value: float | None = None
    execution_costs_complete: bool = False


class RiskLimits(BaseModel):
    max_order_notional: float = Field(gt=0.0)
    max_daily_loss: float = Field(ge=0.0)
    max_drawdown: float = Field(ge=0.0)
    max_data_age_ms: float = Field(gt=0.0)
    max_spread_bps: float = Field(ge=0.0)
    max_slippage_bps: float = Field(ge=0.0)
    minimum_confidence: float = Field(ge=0.0, le=1.0)
    maximum_uncertainty: float = Field(ge=0.0, le=1.0)
    minimum_probability: float = Field(ge=0.0, le=1.0)
    minimum_net_edge: float = Field(ge=0.0)
    minimum_expected_value: float = 0.0


@dataclass(frozen=True)
class RiskRejection:
    reason: str


class DeterministicPreTradeRiskEngine:
    """Independent fail-closed risk policy with no broker or agent authority."""

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits

    def evaluate(
        self,
        *,
        trade_intent: TradeIntent,
        account: AccountRiskSnapshot,
        signal: SignalRiskSnapshot,
    ) -> RiskDecision:
        requested_notional = trade_intent.proposed_quantity * trade_intent.reference_price
        rejection = self._first_rejection(
            requested_notional=requested_notional,
            account=account,
            signal=signal,
        )
        approved = rejection is None
        return RiskDecision(
            decision_id=f"risk-{uuid4()}",
            correlation_id=trade_intent.correlation_id,
            approved=approved,
            reason="APPROVED" if approved else rejection.reason,
            account_state_confirmed=account.account_state_confirmed,
            margin_confirmed=account.margin_confirmed,
            position_state_confirmed=account.position_state_confirmed,
            market_data_fresh=account.market_data_fresh,
            reconciliation_clean=account.reconciliation_clean,
            kill_switch_armed=account.kill_switch_armed,
            requested_notional=requested_notional,
            approved_notional_limit=self._limits.max_order_notional,
            margin_snapshot_id=account.margin_snapshot_id,
        )

    def _first_rejection(
        self,
        *,
        requested_notional: float,
        account: AccountRiskSnapshot,
        signal: SignalRiskSnapshot,
    ) -> RiskRejection | None:
        limits = self._limits
        checks = (
            (account.kill_switch_armed, "KILL_SWITCH_ARMED"),
            (not account.account_state_confirmed, "ACCOUNT_STATE_UNKNOWN"),
            (not account.margin_confirmed, "NO_CONFIRMED_MARGIN"),
            (not account.position_state_confirmed, "UNKNOWN_POSITION"),
            (not account.reconciliation_clean, "RECONCILIATION_FAILURE"),
            (not account.market_data_fresh, "NO_FRESH_MARKET_DATA"),
            (account.buying_power is None, "BUYING_POWER_UNKNOWN"),
            (account.margin_available is None, "MARGIN_AVAILABLE_UNKNOWN"),
            (account.margin_snapshot_id is None, "MARGIN_SNAPSHOT_UNKNOWN"),
            (account.market_data_age_ms is None, "MARKET_DATA_AGE_UNKNOWN"),
            (account.spread_bps is None, "SPREAD_UNKNOWN"),
            (account.estimated_slippage_bps is None, "SLIPPAGE_UNKNOWN"),
            (not signal.execution_costs_complete, "EXECUTION_COSTS_UNKNOWN"),
            (signal.net_edge is None, "NET_EDGE_UNKNOWN"),
            (signal.expected_value is None, "EXPECTED_VALUE_UNKNOWN"),
            (requested_notional > limits.max_order_notional, "ORDER_NOTIONAL_LIMIT"),
            (
                account.buying_power is not None and requested_notional > account.buying_power,
                "BUYING_POWER_LIMIT",
            ),
            (
                account.margin_available is not None
                and requested_notional > account.margin_available,
                "MARGIN_LIMIT",
            ),
            (account.daily_loss >= limits.max_daily_loss, "DAILY_LOSS_LIMIT"),
            (account.drawdown >= limits.max_drawdown, "DRAWDOWN_LIMIT"),
            (
                account.market_data_age_ms is not None
                and account.market_data_age_ms > limits.max_data_age_ms,
                "STALE_MARKET_DATA",
            ),
            (
                account.spread_bps is not None
                and account.spread_bps > limits.max_spread_bps,
                "SPREAD_LIMIT",
            ),
            (
                account.estimated_slippage_bps is not None
                and account.estimated_slippage_bps > limits.max_slippage_bps,
                "SLIPPAGE_LIMIT",
            ),
            (signal.confidence < limits.minimum_confidence, "CONFIDENCE_LIMIT"),
            (signal.uncertainty > limits.maximum_uncertainty, "UNCERTAINTY_LIMIT"),
            (signal.probability < limits.minimum_probability, "PROBABILITY_LIMIT"),
            (
                signal.net_edge is not None and signal.net_edge < limits.minimum_net_edge,
                "NET_EDGE_LIMIT",
            ),
            (
                signal.expected_value is not None
                and signal.expected_value < limits.minimum_expected_value,
                "EXPECTED_VALUE_LIMIT",
            ),
        )
        for failed, reason in checks:
            if failed:
                return RiskRejection(reason)
        return None
