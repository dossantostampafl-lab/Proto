from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FillSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class FillRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    fill_id: str = Field(min_length=1, max_length=128)
    symbol: str = Field(min_length=1, max_length=32)
    side: FillSide
    quantity: Decimal = Field(gt=Decimal("0"))
    price: Decimal = Field(gt=Decimal("0"))
    fee: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    observed_at: datetime

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("observed_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fill timestamp must be timezone-aware")
        return value


class PositionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    quantity: Decimal
    average_price: Decimal = Field(ge=Decimal("0"))
    realized_pnl: Decimal
    fees: Decimal = Field(ge=Decimal("0"))
    net_realized_pnl: Decimal
    mark_price: Decimal | None = Field(default=None, gt=Decimal("0"))
    unrealized_pnl: Decimal | None = None


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    matched: bool
    expected: PositionSnapshot
    actual: PositionSnapshot
    mismatches: tuple[str, ...]


class PositionLedger:
    def __init__(self, symbol: str) -> None:
        normalized = symbol.strip().upper()
        if not normalized:
            raise ValueError("symbol must not be blank")
        self.symbol = normalized
        self.quantity = Decimal("0")
        self.average_price = Decimal("0")
        self.realized_pnl = Decimal("0")
        self.fees = Decimal("0")
        self._fill_ids: set[str] = set()

    def apply_fill(self, fill: FillRecord) -> None:
        if fill.symbol != self.symbol:
            raise ValueError("fill symbol does not match ledger symbol")
        if fill.fill_id in self._fill_ids:
            raise ValueError(f"duplicate fill_id: {fill.fill_id}")

        signed_fill = fill.quantity if fill.side is FillSide.BUY else -fill.quantity
        current = self.quantity

        if current == 0 or current.copy_sign(Decimal("1")) == signed_fill.copy_sign(Decimal("1")):
            total_quantity = abs(current) + fill.quantity
            weighted_cost = abs(current) * self.average_price + fill.quantity * fill.price
            self.average_price = weighted_cost / total_quantity
            self.quantity = current + signed_fill
        else:
            current_sign = Decimal("1") if current > 0 else Decimal("-1")
            closing_quantity = min(abs(current), fill.quantity)
            self.realized_pnl += closing_quantity * (fill.price - self.average_price) * current_sign
            new_quantity = current + signed_fill

            if new_quantity == 0:
                self.quantity = Decimal("0")
                self.average_price = Decimal("0")
            elif (new_quantity > 0) == (current > 0):
                self.quantity = new_quantity
            else:
                self.quantity = new_quantity
                self.average_price = fill.price

        self.fees += fill.fee
        self._fill_ids.add(fill.fill_id)

    def snapshot(self, *, mark_price: Decimal | None = None) -> PositionSnapshot:
        unrealized: Decimal | None = None
        if mark_price is not None:
            if mark_price <= 0:
                raise ValueError("mark_price must be positive")
            if self.quantity == 0:
                unrealized = Decimal("0")
            else:
                position_sign = Decimal("1") if self.quantity > 0 else Decimal("-1")
                unrealized = abs(self.quantity) * (mark_price - self.average_price) * position_sign

        return PositionSnapshot(
            symbol=self.symbol,
            quantity=self.quantity,
            average_price=self.average_price,
            realized_pnl=self.realized_pnl,
            fees=self.fees,
            net_realized_pnl=self.realized_pnl - self.fees,
            mark_price=mark_price,
            unrealized_pnl=unrealized,
        )


def rebuild_position(fills: tuple[FillRecord, ...], *, symbol: str) -> PositionLedger:
    ledger = PositionLedger(symbol)
    for fill in sorted(fills, key=lambda item: (item.observed_at, item.fill_id)):
        ledger.apply_fill(fill)
    return ledger


def reconcile_position(
    fills: tuple[FillRecord, ...],
    *,
    actual: PositionSnapshot,
) -> ReconciliationResult:
    expected = rebuild_position(fills, symbol=actual.symbol).snapshot(mark_price=actual.mark_price)
    fields = (
        "quantity",
        "average_price",
        "realized_pnl",
        "fees",
        "net_realized_pnl",
        "unrealized_pnl",
    )
    mismatches = tuple(
        field for field in fields if getattr(expected, field) != getattr(actual, field)
    )
    return ReconciliationResult(
        matched=not mismatches,
        expected=expected,
        actual=actual,
        mismatches=mismatches,
    )
