from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ExecutionMode(StrEnum):
    SIMULATION = "SIMULATION"
    PAPER_TRADING = "PAPER_TRADING"
    SHADOW = "SHADOW"
    LIVE_CANARY = "LIVE_CANARY"
    LIVE = "LIVE"


class ExecutionEventState(StrEnum):
    RECEIVED = "RECEIVED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class TradeIntent(BaseModel):
    intent_id: str = Field(min_length=8, max_length=128)
    correlation_id: str = Field(min_length=8, max_length=128)
    strategy_id: str = Field(min_length=1, max_length=128)
    instrument: str = Field(min_length=1, max_length=128)
    side: Literal["BUY", "SELL"]
    proposed_quantity: float = Field(gt=0.0)
    reference_price: float = Field(gt=0.0)
    valid_until: datetime
    rationale: str = Field(min_length=1, max_length=2_000)
    model_version: str = Field(min_length=1, max_length=128)
    signature: str = Field(min_length=16, max_length=4_096)

    @model_validator(mode="after")
    def validate_expiry(self) -> TradeIntent:
        valid_until = self.valid_until
        if valid_until.tzinfo is None:
            raise ValueError("valid_until must be timezone-aware")
        if valid_until <= datetime.now(UTC):
            raise ValueError("trade intent is expired")
        return self


class RiskDecision(BaseModel):
    decision_id: str = Field(min_length=8, max_length=128)
    correlation_id: str = Field(min_length=8, max_length=128)
    approved: bool
    reason: str = Field(min_length=1, max_length=2_000)
    account_state_confirmed: bool
    margin_confirmed: bool
    position_state_confirmed: bool
    market_data_fresh: bool
    reconciliation_clean: bool
    kill_switch_armed: bool
    requested_notional: float = Field(ge=0.0)
    approved_notional_limit: float = Field(ge=0.0)
    margin_snapshot_id: str | None = Field(default=None, max_length=256)
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_approval_invariants(self) -> RiskDecision:
        if not self.approved:
            return self
        required = (
            self.account_state_confirmed,
            self.margin_confirmed,
            self.position_state_confirmed,
            self.market_data_fresh,
            self.reconciliation_clean,
        )
        if not all(required):
            raise ValueError("approved risk decision requires all mandatory state confirmations")
        if self.kill_switch_armed:
            raise ValueError("approved risk decision cannot coexist with an armed kill switch")
        if self.requested_notional > self.approved_notional_limit:
            raise ValueError("requested notional exceeds approved risk limit")
        if self.margin_snapshot_id is None:
            raise ValueError("approved risk decision requires a confirmed margin snapshot")
        return self


class OrderIntent(BaseModel):
    order_intent_id: str = Field(min_length=8, max_length=128)
    correlation_id: str = Field(min_length=8, max_length=128)
    idempotency_key: str = Field(min_length=16, max_length=256)
    instrument: str = Field(min_length=1, max_length=128)
    side: Literal["BUY", "SELL"]
    order_type: Literal["LIMIT", "MARKET"]
    quantity: float = Field(gt=0.0)
    limit_price: float | None = Field(default=None, gt=0.0)
    time_in_force: Literal["GTC", "IOC", "FOK", "DAY"]
    expires_at: datetime

    @model_validator(mode="after")
    def validate_order_contract(self) -> OrderIntent:
        if self.order_type == "LIMIT" and self.limit_price is None:
            raise ValueError("limit order requires limit_price")
        if self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        if self.expires_at <= datetime.now(UTC):
            raise ValueError("order intent is expired")
        return self


class ExecutionEvent(BaseModel):
    correlation_id: str = Field(min_length=8, max_length=128)
    order_intent_id: str = Field(min_length=8, max_length=128)
    external_order_id: str | None = Field(default=None, max_length=256)
    state: ExecutionEventState
    event_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_event_digest: str | None = Field(default=None, max_length=256)
