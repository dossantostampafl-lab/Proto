"""Venue adapter contract and the in-memory paper venue.

Only the isolated executor will ever hold an adapter bound to a real venue.
Adapters receive orders that the OMS already authorized with a RiskPermit;
they translate and report, they never decide.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, model_validator

from .contracts import (
    AwareDatetime,
    ExecutionReport,
    OrderStatus,
    OrderType,
    PositiveDecimal,
    TradableInstrument,
    VenueId,
    _Contract,
)

_BPS = Decimal("10000")


class VenueOrder(_Contract):
    """What the OMS sends to a venue after a permit was consumed."""

    client_order_id: str = Field(min_length=1, max_length=36)
    instrument_id: str = Field(min_length=3, max_length=160)
    side: Literal["BUY", "SELL"]
    quantity: PositiveDecimal
    order_type: OrderType
    limit_price: PositiveDecimal | None = None

    @model_validator(mode="after")
    def validate_price(self) -> VenueOrder:
        if (self.order_type is OrderType.LIMIT) != (self.limit_price is not None):
            raise ValueError("limit_price is required exactly for LIMIT orders")
        return self


class Quote(_Contract):
    bid: Decimal = Field(allow_inf_nan=False)
    ask: Decimal = Field(allow_inf_nan=False)
    observed_at: AwareDatetime


@runtime_checkable
class VenueAdapter(Protocol):
    @property
    def venue(self) -> VenueId: ...

    @property
    def real_money_execution(self) -> bool: ...

    async def submit(self, order: VenueOrder) -> ExecutionReport: ...

    async def cancel(self, client_order_id: str) -> ExecutionReport: ...

    async def query(self, client_order_id: str) -> ExecutionReport | None: ...

    async def balances(self) -> dict[str, Decimal]: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PaperVenueAdapter:
    """Deterministic simulated venue: fills marketable orders against a quote.

    Market orders fill at the touch plus slippage, rounded against the trader
    to the tick grid; limit orders fill only when marketable and never worse
    than their limit, otherwise they rest until canceled. Resting orders do
    not reserve balance or fill later: matching belongs to a later phase.
    """

    def __init__(
        self,
        *,
        instruments: tuple[TradableInstrument, ...],
        quotes: Callable[[str], Quote | None],
        balances: Mapping[str, Decimal],
        fee_bps: Decimal,
        slippage_bps: Decimal,
        clock: Callable[[], datetime] = _utc_now,
        simulated_venue: VenueId | None = None,
    ) -> None:
        # A paper venue may stand in for one real venue so PAPER-stage intents for
        # that venue can be executed; it still reports itself as PAPER.
        allowed = {VenueId.PAPER} | ({simulated_venue} if simulated_venue else set())
        if any(instrument.venue not in allowed for instrument in instruments):
            raise ValueError("PaperVenueAdapter only trades PAPER (or its simulated venue)")
        if fee_bps < 0 or slippage_bps < 0:
            raise ValueError("fee_bps and slippage_bps must be non-negative")
        self._instruments = {item.instrument_id: item for item in instruments}
        self._quotes = quotes
        self._balances = {asset: Decimal(amount) for asset, amount in balances.items()}
        self._fee_bps = fee_bps
        self._slippage_bps = slippage_bps
        self._clock = clock
        self._orders: dict[str, tuple[VenueOrder, ExecutionReport]] = {}

    @property
    def venue(self) -> VenueId:
        return VenueId.PAPER

    @property
    def real_money_execution(self) -> bool:
        return False

    async def submit(self, order: VenueOrder) -> ExecutionReport:
        existing = self._orders.get(order.client_order_id)
        if existing is not None:
            if existing[0] != order:
                raise ValueError("client_order_id already belongs to a different order")
            return existing[1]
        report = self._execute(order)
        self._orders[order.client_order_id] = (order, report)
        return report

    async def cancel(self, client_order_id: str) -> ExecutionReport:
        order, report = self._orders[client_order_id]
        if report.status is not OrderStatus.ACKNOWLEDGED:
            return report
        canceled = report.model_copy(
            update={"status": OrderStatus.CANCELED, "reported_at": self._clock()}
        )
        self._orders[client_order_id] = (order, canceled)
        return canceled

    async def query(self, client_order_id: str) -> ExecutionReport | None:
        entry = self._orders.get(client_order_id)
        return entry[1] if entry is not None else None

    async def balances(self) -> dict[str, Decimal]:
        return dict(self._balances)

    def _report(
        self,
        order: VenueOrder,
        status: OrderStatus,
        *,
        price: Decimal | None = None,
        fee: Decimal = Decimal("0"),
        fee_asset: str | None = None,
        reason: str | None = None,
    ) -> ExecutionReport:
        return ExecutionReport(
            venue=VenueId.PAPER,
            client_order_id=order.client_order_id,
            venue_order_id=f"paper-{order.client_order_id}",
            instrument_id=order.instrument_id,
            side=order.side,
            status=status,
            quantity=order.quantity,
            filled_quantity=order.quantity if status is OrderStatus.FILLED else Decimal("0"),
            average_price=price,
            fee=fee,
            fee_asset=fee_asset,
            reason=reason,
            reported_at=self._clock(),
        )

    def _reject(self, order: VenueOrder, reason: str) -> ExecutionReport:
        return self._report(order, OrderStatus.REJECTED, reason=reason)

    def _execute(self, order: VenueOrder) -> ExecutionReport:
        instrument = self._instruments.get(order.instrument_id)
        if instrument is None:
            return self._reject(order, "unknown instrument")
        quote = self._quotes(order.instrument_id)
        if quote is None:
            return self._reject(order, "no quote available")
        if not (0 < quote.bid < quote.ask):
            return self._reject(order, "crossed or invalid quote")

        buying = order.side == "BUY"
        touch = quote.ask if buying else quote.bid
        direction = Decimal("1") if buying else Decimal("-1")
        raw = touch * (1 + direction * self._slippage_bps / _BPS)
        rounding = ROUND_CEILING if buying else ROUND_FLOOR
        price = (raw / instrument.tick_size).to_integral_value(rounding) * instrument.tick_size

        if order.order_type is OrderType.LIMIT:
            assert order.limit_price is not None
            marketable = touch <= order.limit_price if buying else touch >= order.limit_price
            if not marketable:
                violation = instrument.order_violation(order.quantity, order.limit_price)
                if violation is not None:
                    return self._reject(order, violation)
                return self._report(order, OrderStatus.ACKNOWLEDGED)
            price = min(price, order.limit_price) if buying else max(price, order.limit_price)

        violation = instrument.order_violation(order.quantity, price)
        if violation is not None:
            return self._reject(order, violation)

        notional = order.quantity * price
        fee = notional * self._fee_bps / _BPS
        base, quote_asset = instrument.base_asset, instrument.quote_asset
        quote_balance = self._balances.get(quote_asset, Decimal("0"))
        base_balance = self._balances.get(base, Decimal("0"))
        if buying and quote_balance < notional + fee:
            return self._reject(order, f"insufficient {quote_asset} balance")
        if not buying and base_balance < order.quantity:
            return self._reject(order, f"insufficient {base} balance")

        signed_quantity = order.quantity if buying else -order.quantity
        self._balances[base] = base_balance + signed_quantity
        self._balances[quote_asset] = quote_balance - direction * notional - fee
        return self._report(order, OrderStatus.FILLED, price=price, fee=fee, fee_asset=quote_asset)
