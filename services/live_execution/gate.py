from __future__ import annotations

from pydantic import BaseModel, Field

from .contracts import ExecutionMode, OrderIntent, RiskDecision, TradeIntent


class LiveExecutionConfig(BaseModel):
    mode: ExecutionMode = ExecutionMode.SIMULATION
    live_execution_enabled: bool = False
    custody_enabled: bool = False
    withdrawals_enabled: bool = False
    human_activation_required: bool = True
    human_activation_recorded: bool = False
    adapter_validated: bool = False
    permissions_validated: bool = False
    canary_max_notional: float = Field(default=0.0, ge=0.0)
    live_max_notional: float = Field(default=0.0, ge=0.0)


class LiveGateDecision(BaseModel):
    allowed: bool
    reason: str
    mode: ExecutionMode


class LiveExecutionGate:
    """Fail-closed policy gate with no broker connectivity or credential access."""

    def __init__(self, config: LiveExecutionConfig) -> None:
        self._config = config
        self._seen_idempotency_keys: set[str] = set()

    def evaluate(
        self,
        *,
        trade_intent: TradeIntent,
        risk_decision: RiskDecision,
        order_intent: OrderIntent,
    ) -> LiveGateDecision:
        config = self._config

        if config.mode not in {ExecutionMode.LIVE_CANARY, ExecutionMode.LIVE}:
            return self._deny("execution mode is not live-capable")
        if not config.live_execution_enabled:
            return self._deny("LIVE_EXECUTION_ENABLED is false")
        if config.custody_enabled:
            return self._deny("custody must remain disabled")
        if config.withdrawals_enabled:
            return self._deny("withdrawals must remain disabled")
        if not config.human_activation_required:
            return self._deny("human activation requirement cannot be disabled")
        if not config.human_activation_recorded:
            return self._deny("human activation has not been recorded")
        if not config.adapter_validated:
            return self._deny("official adapter has not been validated")
        if not config.permissions_validated:
            return self._deny("adapter permissions have not been validated")
        if not risk_decision.approved:
            return self._deny("risk engine rejected the intent")
        if trade_intent.correlation_id != risk_decision.correlation_id:
            return self._deny("trade and risk correlation IDs differ")
        if trade_intent.correlation_id != order_intent.correlation_id:
            return self._deny("trade and order correlation IDs differ")
        if trade_intent.instrument != order_intent.instrument:
            return self._deny("trade and order instruments differ")
        if trade_intent.side != order_intent.side:
            return self._deny("trade and order sides differ")
        if order_intent.idempotency_key in self._seen_idempotency_keys:
            return self._deny("duplicate idempotency key must not be resent")

        order_notional = order_intent.quantity * (
            order_intent.limit_price or trade_intent.reference_price
        )
        configured_limit = (
            config.canary_max_notional
            if config.mode == ExecutionMode.LIVE_CANARY
            else config.live_max_notional
        )
        if configured_limit <= 0.0:
            return self._deny("live notional limit is not configured")
        if order_notional > configured_limit:
            return self._deny("order exceeds configured live notional limit")
        if order_notional > risk_decision.approved_notional_limit:
            return self._deny("order exceeds approved risk notional limit")

        self._seen_idempotency_keys.add(order_intent.idempotency_key)
        return LiveGateDecision(
            allowed=True,
            reason="all live execution gates satisfied",
            mode=config.mode,
        )

    def _deny(self, reason: str) -> LiveGateDecision:
        return LiveGateDecision(allowed=False, reason=reason, mode=self._config.mode)
