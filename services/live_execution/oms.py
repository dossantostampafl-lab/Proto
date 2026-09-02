from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from .contracts import OrderIntent


class OrderState(StrEnum):
    CREATED = "CREATED"
    SUBMIT_PENDING = "SUBMIT_PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    HALTED = "HALTED"


class OrderRecord(BaseModel):
    order_intent: OrderIntent
    state: OrderState = OrderState.CREATED
    external_order_id: str | None = Field(default=None, max_length=256)
    filled_quantity: float = Field(default=0.0, ge=0.0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


_ALLOWED_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.CREATED: {OrderState.SUBMIT_PENDING, OrderState.HALTED, OrderState.EXPIRED},
    OrderState.SUBMIT_PENDING: {
        OrderState.ACKNOWLEDGED,
        OrderState.REJECTED,
        OrderState.HALTED,
    },
    OrderState.ACKNOWLEDGED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCEL_PENDING,
        OrderState.HALTED,
    },
    OrderState.PARTIALLY_FILLED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCEL_PENDING,
        OrderState.HALTED,
    },
    OrderState.CANCEL_PENDING: {
        OrderState.CANCELLED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.HALTED,
    },
    OrderState.FILLED: set(),
    OrderState.CANCELLED: set(),
    OrderState.REJECTED: set(),
    OrderState.EXPIRED: set(),
    OrderState.HALTED: set(),
}


class OrderManagementSystem:
    """Deterministic OMS state machine; it never performs network submission."""

    def __init__(self) -> None:
        self._orders: dict[str, OrderRecord] = {}
        self._idempotency_keys: set[str] = set()

    def register(self, order_intent: OrderIntent) -> OrderRecord:
        if order_intent.idempotency_key in self._idempotency_keys:
            raise ValueError("duplicate idempotency key")
        if order_intent.order_intent_id in self._orders:
            raise ValueError("duplicate order intent id")
        record = OrderRecord(order_intent=order_intent)
        self._orders[order_intent.order_intent_id] = record
        self._idempotency_keys.add(order_intent.idempotency_key)
        return record.model_copy(deep=True)

    def transition(
        self,
        order_intent_id: str,
        next_state: OrderState,
        *,
        external_order_id: str | None = None,
        filled_quantity: float | None = None,
    ) -> OrderRecord:
        record = self._orders.get(order_intent_id)
        if record is None:
            raise KeyError("unknown order intent")
        if next_state not in _ALLOWED_TRANSITIONS[record.state]:
            raise ValueError(f"invalid order transition: {record.state} -> {next_state}")
        if filled_quantity is not None:
            if filled_quantity < record.filled_quantity:
                raise ValueError("filled quantity cannot decrease")
            if filled_quantity > record.order_intent.quantity:
                raise ValueError("filled quantity exceeds order quantity")
            record.filled_quantity = filled_quantity
        if (
            next_state == OrderState.FILLED
            and record.filled_quantity != record.order_intent.quantity
        ):
            raise ValueError("FILLED requires complete filled quantity")
        if external_order_id is not None:
            if record.external_order_id not in {None, external_order_id}:
                raise ValueError("external order id cannot change")
            record.external_order_id = external_order_id
        record.state = next_state
        record.updated_at = datetime.now(UTC)
        return record.model_copy(deep=True)

    def get(self, order_intent_id: str) -> OrderRecord:
        record = self._orders.get(order_intent_id)
        if record is None:
            raise KeyError("unknown order intent")
        return record.model_copy(deep=True)

    def snapshot(self) -> list[OrderRecord]:
        return [record.model_copy(deep=True) for record in self._orders.values()]
