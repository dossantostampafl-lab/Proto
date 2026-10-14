"""Canonical Order Management System.

The OMS owns order state. For every order it:

1. consumes the Risk Permit (signature, fingerprint, expiry, single use);
2. reserves funds in the ledger (rejects without touching the venue if short);
3. submits with a deterministic client order id (resubmission is idempotent);
4. applies venue reports through the order state machine, posting fills,
   fees and reservation releases to the ledger;
5. resolves UNKNOWN only by querying the venue - never by resending.

Real-money adapters are refused: no live scope is certified yet.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_UP, Decimal
from typing import Any

from services.execution import (
    ExecutionReport,
    IllegalOrderTransition,
    LedgerEntry,
    LedgerEntryKind,
    OrderStatus,
    RiskPermit,
    TradableInstrument,
    TradeIntent,
    transition,
)
from services.execution.contracts import TERMINAL_ORDER_STATUSES
from services.execution.venue import VenueAdapter, VenueOrder
from services.risk import PermitLedger, PermitRejected

from .ledger import InMemoryLedger, LedgerRejected, SqlLedger, entry_id

logger = logging.getLogger("proto.oms")

# Reserve a little above the limit notional so fees never exceed the hold.
FEE_RESERVE = Decimal("0.005")


@dataclass
class ManagedOrder:
    intent: TradeIntent
    client_order_id: str
    status: OrderStatus
    reserved_asset: str
    reserved: Decimal
    released: Decimal = Decimal("0")
    filled_quantity: Decimal = Decimal("0")
    filled_notional: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")
    venue_order_id: str | None = None
    reason: str | None = None
    history: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "intent_id": str(self.intent.intent_id),
            "instrument_id": self.intent.instrument_id,
            "side": self.intent.side,
            "quantity": str(self.intent.quantity),
            "limit_price": str(self.intent.limit_price) if self.intent.limit_price else None,
            "status": self.status.value,
            "filled_quantity": str(self.filled_quantity),
            "average_price": (
                str(self.filled_notional / self.filled_quantity) if self.filled_quantity else None
            ),
            "fee": str(self.fee),
            "reserved": str(self.reserved - self.released),
            "venue_order_id": self.venue_order_id,
            "reason": self.reason,
            "history": list(self.history),
            "real_money_execution": False,
        }


class OrderRejected(Exception):
    def __init__(self, code: str, order: ManagedOrder | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.order = order


Recorder = Callable[[str, dict[str, Any]], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OrderManager:
    def __init__(
        self,
        *,
        adapter: VenueAdapter,
        ledger: InMemoryLedger | SqlLedger,
        permits: PermitLedger,
        instruments: Mapping[str, TradableInstrument],
        recorder: Recorder | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if adapter.real_money_execution:
            raise ValueError("no certified live scope: real-money adapters are refused")
        self._adapter = adapter
        self._ledger = ledger
        self._permits = permits
        self._instruments = dict(instruments)
        self._recorder = recorder
        self._clock = clock
        self._orders: dict[str, ManagedOrder] = {}
        self._by_intent: dict[str, str] = {}
        self._lock = asyncio.Lock()

    @property
    def orders(self) -> list[ManagedOrder]:
        return list(self._orders.values())

    def get(self, client_order_id: str) -> ManagedOrder | None:
        return self._orders.get(client_order_id)

    async def submit(self, intent: TradeIntent, permit: RiskPermit) -> ManagedOrder:
        async with self._lock:
            existing = self._by_intent.get(str(intent.intent_id))
            if existing is not None:
                return self._orders[existing]
            now = self._clock()
            try:
                self._permits.consume(permit, intent, now=now)
            except PermitRejected as exc:
                await self._record(
                    "ORDER_REFUSED",
                    {"intent_id": str(intent.intent_id), "reason": f"permit_{exc.code}"},
                )
                raise OrderRejected(f"permit_{exc.code}") from exc
            instrument = self._instruments.get(intent.instrument_id)
            if instrument is None or intent.limit_price is None:
                raise OrderRejected("unsupported_intent")

            order = self._new_order(intent, instrument)
            self._orders[order.client_order_id] = order
            self._by_intent[str(intent.intent_id)] = order.client_order_id
            try:
                await self._ledger.post([self._reservation(order, now)])
            except LedgerRejected as exc:
                self._set_status(order, OrderStatus.REJECTED, reason=str(exc))
                await self._record("ORDER_REJECTED", order.to_dict())
                return order

            self._set_status(order, OrderStatus.SUBMITTED)
            await self._record("ORDER_SUBMITTED", order.to_dict())
            try:
                report = await self._adapter.submit(
                    VenueOrder(
                        client_order_id=order.client_order_id,
                        instrument_id=intent.instrument_id,
                        side=intent.side,
                        quantity=intent.quantity,
                        order_type=intent.order_type,
                        limit_price=intent.limit_price,
                    )
                )
            except Exception as exc:  # timeout, network, venue outage
                logger.warning("submit outcome unknown for %s: %s", order.client_order_id, exc)
                self._set_status(order, OrderStatus.UNKNOWN, reason=type(exc).__name__)
                await self._record("ORDER_UNKNOWN", order.to_dict())
                return order
            await self._apply(order, report)
            return order

    async def resolve_unknown(self, client_order_id: str) -> ManagedOrder:
        """Ask the venue what happened. Never resend an order in UNKNOWN."""

        async with self._lock:
            order = self._orders[client_order_id]
            if order.status is not OrderStatus.UNKNOWN:
                return order
            report = await self._adapter.query(client_order_id)
            if report is None:
                # The venue confirms it never received the order.
                self._set_status(order, OrderStatus.REJECTED, reason="not found at venue")
                await self._release_rest(order)
                await self._record("ORDER_REJECTED", order.to_dict())
                return order
            await self._apply(order, report)
            return order

    async def cancel(self, client_order_id: str) -> ManagedOrder:
        async with self._lock:
            order = self._orders[client_order_id]
            if order.status in TERMINAL_ORDER_STATUSES or order.status is OrderStatus.UNKNOWN:
                return order
            report = await self._adapter.cancel(client_order_id)
            await self._apply(order, report)
            return order

    def _new_order(self, intent: TradeIntent, instrument: TradableInstrument) -> ManagedOrder:
        assert intent.limit_price is not None
        if intent.side == "BUY":
            asset = instrument.quote_asset
            amount = (intent.quantity * intent.limit_price * (1 + FEE_RESERVE)).quantize(
                Decimal("0.00000001"), rounding=ROUND_UP
            )
        else:
            asset, amount = instrument.base_asset, intent.quantity
        return ManagedOrder(
            intent=intent,
            client_order_id=intent.client_order_id(0),
            status=OrderStatus.NEW,
            reserved_asset=asset,
            reserved=amount,
            history=[OrderStatus.NEW.value],
        )

    def _entry(
        self,
        order: ManagedOrder,
        kind: LedgerEntryKind,
        asset: str,
        amount: Decimal,
        now: datetime,
        *key: object,
    ) -> LedgerEntry:
        return LedgerEntry(
            entry_id=entry_id(order.client_order_id, kind.value, asset, *key),
            account_id=self._ledger.account_id,
            venue=self._ledger.venue,
            asset=asset,
            amount=amount,
            kind=kind,
            reference_id=order.client_order_id,
            recorded_at=now,
        )

    def _reservation(self, order: ManagedOrder, now: datetime) -> LedgerEntry:
        return self._entry(
            order, LedgerEntryKind.RESERVATION, order.reserved_asset, -order.reserved, now
        )

    def _set_status(self, order: ManagedOrder, status: OrderStatus, *, reason=None) -> None:
        if status is not order.status:
            order.status = transition(order.status, status)
            order.history.append(status.value)
        if reason is not None:
            order.reason = reason

    async def _apply(self, order: ManagedOrder, report: ExecutionReport) -> None:
        if report.client_order_id != order.client_order_id:
            raise ValueError("report belongs to another order")
        now = self._clock()
        order.venue_order_id = report.venue_order_id or order.venue_order_id
        increment = report.filled_quantity - order.filled_quantity
        if increment < 0:
            logger.error("venue reported a decreasing fill for %s", order.client_order_id)
            return
        if increment > 0:
            await self._post_fill(order, report, increment, now)
        try:
            self._set_status(order, report.status, reason=report.reason)
        except IllegalOrderTransition:
            logger.error(
                "illegal transition %s -> %s for %s",
                order.status,
                report.status,
                order.client_order_id,
            )
            return
        if order.status in TERMINAL_ORDER_STATUSES:
            await self._release_rest(order)
        await self._record(f"ORDER_{order.status.value}", order.to_dict())

    async def _post_fill(
        self, order: ManagedOrder, report: ExecutionReport, increment: Decimal, now: datetime
    ) -> None:
        instrument = self._instruments[order.intent.instrument_id]
        assert report.average_price is not None
        cumulative_notional = report.filled_quantity * report.average_price
        notional = cumulative_notional - order.filled_notional
        fee = report.fee - order.fee
        buying = order.intent.side == "BUY"
        mark = report.filled_quantity  # makes entry ids unique per cumulative fill
        base, quote = instrument.base_asset, instrument.quote_asset
        release = min(
            order.reserved * increment / order.intent.quantity, order.reserved - order.released
        )
        trade = LedgerEntryKind.TRADE
        batch = [
            self._entry(order, trade, base, increment if buying else -increment, now, mark),
            self._entry(order, trade, quote, -notional if buying else notional, now, mark),
        ]
        if fee > 0:
            batch.append(self._entry(order, LedgerEntryKind.FEE, quote, -fee, now, mark))
        if release > 0:
            batch.append(
                self._entry(
                    order, LedgerEntryKind.RELEASE, order.reserved_asset, release, now, mark
                )
            )
        await self._ledger.post(batch)
        order.filled_quantity = report.filled_quantity
        order.filled_notional = cumulative_notional
        order.fee = report.fee
        order.released += release

    async def _release_rest(self, order: ManagedOrder) -> None:
        rest = order.reserved - order.released
        if rest > 0:
            await self._ledger.post(
                [
                    self._entry(
                        order,
                        LedgerEntryKind.RELEASE,
                        order.reserved_asset,
                        rest,
                        self._clock(),
                        "final",
                    )
                ]
            )
            order.released += rest

    async def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._recorder is not None:
            await self._recorder(event_type, payload)
