from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel

from .contracts import ExecutionMode, OrderIntent


class AdapterEnvironment(StrEnum):
    SHADOW = "SHADOW"
    SANDBOX = "SANDBOX"
    PRODUCTION = "PRODUCTION"


class AdapterCapabilities(BaseModel):
    account_state: bool = False
    margin_state: bool = False
    positions: bool = False
    open_orders: bool = False
    place_order: bool = False
    amend_order: bool = False
    cancel_order: bool = False
    fills: bool = False
    execution_stream: bool = False
    reconciliation: bool = False


class AdapterDescriptor(BaseModel):
    name: str
    environment: AdapterEnvironment
    official_api: bool
    capabilities: AdapterCapabilities


class BrokerExchangeAdapter(Protocol):
    @property
    def descriptor(self) -> AdapterDescriptor: ...

    def validate_connection(self) -> bool: ...

    def validate_permissions(self) -> bool: ...


class ShadowExecutionAdapter:
    """No-network adapter that records intentions and never submits an order."""

    def __init__(self) -> None:
        self._intents: list[OrderIntent] = []
        self._descriptor = AdapterDescriptor(
            name="shadow-no-network",
            environment=AdapterEnvironment.SHADOW,
            official_api=False,
            capabilities=AdapterCapabilities(),
        )

    @property
    def descriptor(self) -> AdapterDescriptor:
        return self._descriptor

    def validate_connection(self) -> bool:
        return True

    def validate_permissions(self) -> bool:
        return True

    def record_intent(self, order_intent: OrderIntent) -> None:
        self._intents.append(order_intent.model_copy(deep=True))

    def intents(self) -> list[OrderIntent]:
        return [intent.model_copy(deep=True) for intent in self._intents]


class AdapterRegistry:
    """Registry that prevents execution-capable adapters in non-live modes."""

    def __init__(self, mode: ExecutionMode) -> None:
        self._mode = mode
        self._adapters: dict[str, BrokerExchangeAdapter] = {}

    def register(self, adapter: BrokerExchangeAdapter) -> None:
        descriptor = adapter.descriptor
        execution_capable = descriptor.capabilities.place_order
        if self._mode in {
            ExecutionMode.SIMULATION,
            ExecutionMode.PAPER_TRADING,
            ExecutionMode.SHADOW,
        } and execution_capable:
            raise ValueError("execution-capable adapter cannot register in non-live mode")
        if descriptor.environment == AdapterEnvironment.PRODUCTION and self._mode not in {
            ExecutionMode.LIVE_CANARY,
            ExecutionMode.LIVE,
        }:
            raise ValueError("production adapter requires a live-capable mode")
        if descriptor.name in self._adapters:
            raise ValueError("duplicate adapter registration")
        self._adapters[descriptor.name] = adapter

    def descriptors(self) -> list[AdapterDescriptor]:
        return [adapter.descriptor.model_copy(deep=True) for adapter in self._adapters.values()]
