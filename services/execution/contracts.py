"""Canonical execution contracts shared by every venue path.

These types describe the authority chain of the execution-boundary ADR:
signal -> typed intent -> risk permit -> OMS order -> venue report -> ledger.
Defining a contract does not connect PROTO to any venue; real-money execution
stays disabled until a scoped path passes its release gates.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from services.market_data.instruments import AssetClass, SessionType

SCHEMA_VERSION = "1"
MAX_PERMIT_LIFETIME = timedelta(seconds=60)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must carry a timezone")
    return value


AwareDatetime = Annotated[datetime, AfterValidator(_aware)]
PositiveDecimal = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")


class VenueId(StrEnum):
    PAPER = "PAPER"
    BINANCE_SPOT = "BINANCE_SPOT"
    XP_B3 = "XP_B3"


class ReleaseStage(StrEnum):
    RESEARCH = "RESEARCH"
    REPLAY = "REPLAY"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    CANARY = "CANARY"
    LIVE = "LIVE"


def _require_namespace(instrument_id: str, venue: VenueId) -> None:
    if not instrument_id.startswith(f"{venue.value}:"):
        raise ValueError("instrument_id must be namespaced by its venue, e.g. VENUE:SYMBOL")


class TradableInstrument(_Contract):
    """Venue-specific order grid for one instrument."""

    schema_version: Literal["1"] = SCHEMA_VERSION
    instrument_id: str = Field(min_length=3, max_length=160)
    venue: VenueId
    asset_class: AssetClass
    base_asset: str = Field(min_length=1, max_length=20)
    quote_asset: str = Field(min_length=3, max_length=10)
    tick_size: PositiveDecimal
    lot_size: PositiveDecimal
    min_quantity: PositiveDecimal
    min_notional: PositiveDecimal
    session_type: SessionType
    timezone: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_identity(self) -> TradableInstrument:
        _require_namespace(self.instrument_id, self.venue)
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        if self.asset_class is AssetClass.PREDICTION_MARKET:
            raise ValueError("prediction markets require separate legal and venue gates")
        return self

    def order_violation(self, quantity: Decimal, price: Decimal) -> str | None:
        """Return why an order does not fit the venue grid, or None if it does."""

        if quantity < self.min_quantity:
            return "quantity below venue minimum"
        if quantity % self.lot_size != 0:
            return "quantity not on lot grid"
        if price <= 0 or price % self.tick_size != 0:
            return "price not on tick grid"
        if quantity * price < self.min_notional:
            return "notional below venue minimum"
        return None


class SignalSource(StrEnum):
    TRADINGVIEW = "TRADINGVIEW"
    PROTO_STRATEGY = "PROTO_STRATEGY"
    PROTO_AGENT = "PROTO_AGENT"


class SignalDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class Signal(_Contract):
    """An opinion about an instrument. A signal never carries order authority."""

    schema_version: Literal["1"] = SCHEMA_VERSION
    signal_id: UUID
    source: SignalSource
    strategy_id: str = Field(min_length=1, max_length=120)
    instrument_id: str = Field(min_length=3, max_length=160)
    direction: SignalDirection
    strength: float = Field(ge=0.0, le=1.0)
    timeframe: str = Field(min_length=1, max_length=20)
    observed_at: AwareDatetime
    received_at: AwareDatetime

    @model_validator(mode="after")
    def validate_clock(self) -> Signal:
        if self.received_at < self.observed_at:
            raise ValueError("received_at cannot precede observed_at")
        return self


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class TimeInForce(StrEnum):
    GTC = "GTC"
    IOC = "IOC"
    DAY = "DAY"


class TradeIntent(_Contract):
    """Typed request to trade. Only the Risk Engine can turn it into an order."""

    schema_version: Literal["1"] = SCHEMA_VERSION
    intent_id: UUID
    instrument_id: str = Field(min_length=3, max_length=160)
    venue: VenueId
    side: Literal["BUY", "SELL"]
    quantity: PositiveDecimal
    order_type: OrderType
    limit_price: PositiveDecimal | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    strategy_id: str = Field(min_length=1, max_length=120)
    model_version: str = Field(min_length=1, max_length=80)
    signal_ids: tuple[UUID, ...] = Field(min_length=1, max_length=64)
    stage: ReleaseStage = ReleaseStage.PAPER
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_shape(self) -> TradeIntent:
        _require_namespace(self.instrument_id, self.venue)
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit_price is required for LIMIT intents")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("limit_price is not allowed for MARKET intents")
        return self

    def fingerprint(self) -> str:
        """SHA-256 over the canonical JSON of every field; binds permits to intents."""

        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def client_order_id(self, attempt: int) -> str:
        """Deterministic venue client id; resubmitting an attempt is idempotent."""

        if attempt < 0:
            raise ValueError("attempt must be non-negative")
        digest = hashlib.sha256(f"{self.intent_id}:{attempt}".encode()).hexdigest()
        return f"PX{digest[:30]}"


class RiskPermit(_Contract):
    """Short-lived, single-use authorization bound to one exact intent."""

    schema_version: Literal["1"] = SCHEMA_VERSION
    permit_id: UUID
    intent_id: UUID
    intent_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    max_quantity: PositiveDecimal
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lifetime(self) -> RiskPermit:
        lifetime = self.expires_at - self.issued_at
        if lifetime <= timedelta(0) or lifetime > MAX_PERMIT_LIFETIME:
            raise ValueError("permit lifetime must be positive and at most 60 seconds")
        return self

    def covers(self, intent: TradeIntent, *, now: datetime) -> bool:
        """Structural coverage only; signature and single use are OMS checks."""

        return (
            self.intent_id == intent.intent_id
            and self.intent_fingerprint == intent.fingerprint()
            and intent.quantity <= self.max_quantity
            and self.issued_at <= _aware(now) < self.expires_at
        )


class OrderStatus(StrEnum):
    NEW = "NEW"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


TERMINAL_ORDER_STATUSES = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED}
)

_VENUE_CONFIRMED = frozenset(
    {
        OrderStatus.ACKNOWLEDGED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
    }
)

_ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.NEW: frozenset({OrderStatus.SUBMITTED, OrderStatus.REJECTED}),
    OrderStatus.SUBMITTED: _VENUE_CONFIRMED | {OrderStatus.UNKNOWN},
    OrderStatus.ACKNOWLEDGED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.UNKNOWN,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.UNKNOWN,
        }
    ),
    # UNKNOWN is resolved only by querying the venue, never by resubmitting.
    OrderStatus.UNKNOWN: _VENUE_CONFIRMED,
}


class IllegalOrderTransition(ValueError):
    pass


def transition(current: OrderStatus, new: OrderStatus) -> OrderStatus:
    if new not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise IllegalOrderTransition(f"{current.value} -> {new.value} is not allowed")
    return new


class ExecutionReport(_Contract):
    """Venue-confirmed view of one order, cumulative over its fills."""

    schema_version: Literal["1"] = SCHEMA_VERSION
    venue: VenueId
    client_order_id: str = Field(min_length=1, max_length=36)
    venue_order_id: str | None = Field(default=None, max_length=80)
    instrument_id: str = Field(min_length=3, max_length=160)
    side: Literal["BUY", "SELL"]
    status: OrderStatus
    quantity: PositiveDecimal
    filled_quantity: Decimal = Field(ge=0, allow_inf_nan=False)
    average_price: PositiveDecimal | None = None
    fee: Decimal = Field(default=Decimal("0"), ge=0, allow_inf_nan=False)
    fee_asset: str | None = Field(default=None, max_length=10)
    reason: str | None = Field(default=None, max_length=200)
    reported_at: AwareDatetime

    @model_validator(mode="after")
    def validate_fills(self) -> ExecutionReport:
        if self.filled_quantity > self.quantity:
            raise ValueError("filled_quantity cannot exceed quantity")
        if (self.filled_quantity > 0) != (self.average_price is not None):
            raise ValueError("average_price is required exactly when something filled")
        if self.status is OrderStatus.FILLED and self.filled_quantity != self.quantity:
            raise ValueError("FILLED requires the full quantity")
        if self.status is OrderStatus.PARTIALLY_FILLED and not (
            0 < self.filled_quantity < self.quantity
        ):
            raise ValueError("PARTIALLY_FILLED requires a partial quantity")
        return self


class LedgerEntryKind(StrEnum):
    TRADE = "TRADE"
    FEE = "FEE"
    RESERVATION = "RESERVATION"
    RELEASE = "RELEASE"


class LedgerEntry(_Contract):
    """One signed balance movement; positions and balances are sums of entries."""

    schema_version: Literal["1"] = SCHEMA_VERSION
    entry_id: UUID
    account_id: str = Field(min_length=1, max_length=80)
    venue: VenueId
    asset: str = Field(min_length=1, max_length=20)
    amount: Decimal = Field(allow_inf_nan=False)
    kind: LedgerEntryKind
    reference_id: str = Field(min_length=1, max_length=80)
    recorded_at: AwareDatetime

    @field_validator("amount")
    @classmethod
    def validate_non_zero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("ledger entries must move a non-zero amount")
        return value

    @model_validator(mode="after")
    def validate_sign(self) -> LedgerEntry:
        if self.kind is LedgerEntryKind.FEE and self.amount > 0:
            raise ValueError("a fee entry must debit the account")
        if self.kind is LedgerEntryKind.RESERVATION and self.amount > 0:
            raise ValueError("a reservation entry must debit available balance")
        if self.kind is LedgerEntryKind.RELEASE and self.amount < 0:
            raise ValueError("a release entry must credit available balance")
        return self
