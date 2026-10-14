"""Durable OMS state: consumed permits, orders and equity checkpoints.

Without these the OMS would forget, on restart, which permits were spent,
which orders were in flight and where the daily-loss and drawdown baselines
stood. All three tables live next to the ledger (migration `0009_oms_state`).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Column, Date, DateTime, String, Table, Text, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from services.execution import OrderStatus, RiskPermit, TradeIntent
from services.risk import PermitLedger, PermitRejected

from .ledger import ledger_metadata

if TYPE_CHECKING:
    from .manager import ManagedOrder

# Decimals are stored as their exact text: SQLite's NUMERIC goes through float.
_DECIMAL = String(64)

consumed_permits_table = Table(
    "oms_consumed_permits",
    ledger_metadata,
    Column("permit_id", String(36), primary_key=True),
    Column("intent_id", String(36), nullable=False, index=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True), nullable=False),
)

orders_table = Table(
    "oms_orders",
    ledger_metadata,
    Column("client_order_id", String(36), primary_key=True),
    Column("account_id", String(80), nullable=False, index=True),
    Column("intent_id", String(36), nullable=False, unique=True),
    Column("intent", Text, nullable=False),
    Column("status", String(24), nullable=False),
    Column("reserved_asset", String(20), nullable=False),
    Column("reserved", _DECIMAL, nullable=False),
    Column("released", _DECIMAL, nullable=False),
    Column("filled_quantity", _DECIMAL, nullable=False),
    Column("filled_notional", _DECIMAL, nullable=False),
    Column("fee", _DECIMAL, nullable=False),
    Column("venue_order_id", String(80), nullable=True),
    Column("reason", String(200), nullable=True),
    Column("history", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

equity_checkpoints_table = Table(
    "oms_equity_checkpoints",
    ledger_metadata,
    Column("account_id", String(80), primary_key=True),
    Column("quote_asset", String(20), primary_key=True),
    Column("day", Date, nullable=False),
    Column("start_of_day_equity", _DECIMAL, nullable=False),
    Column("peak_equity", _DECIMAL, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


class SqlPermitLedger:
    """Single use that survives restarts and is shared by every OMS process.

    The signature, fingerprint and expiry are checked by the in-process ledger's
    signer; the spend is a primary-key insert committed before the order is
    sent, so a second use is refused even after a restart or by another writer.
    """

    def __init__(self, verifier: PermitLedger, engine: AsyncEngine) -> None:
        self._verifier = verifier
        self._engine = engine

    async def consume(self, permit: RiskPermit, intent: TradeIntent, *, now: datetime) -> None:
        self._verifier.verify(permit, intent, now=now)
        try:
            async with self._engine.begin() as connection:
                await connection.execute(
                    consumed_permits_table.insert().values(
                        permit_id=str(permit.permit_id),
                        intent_id=str(permit.intent_id),
                        expires_at=permit.expires_at,
                        consumed_at=now,
                    )
                )
        except IntegrityError as exc:
            raise PermitRejected("already_used") from exc


def _order_row(order: ManagedOrder, account_id: str) -> dict[str, object]:
    return {
        "client_order_id": order.client_order_id,
        "account_id": account_id,
        "intent_id": str(order.intent.intent_id),
        "intent": order.intent.model_dump_json(),
        "status": order.status.value,
        "reserved_asset": order.reserved_asset,
        "reserved": str(order.reserved),
        "released": str(order.released),
        "filled_quantity": str(order.filled_quantity),
        "filled_notional": str(order.filled_notional),
        "fee": str(order.fee),
        "venue_order_id": order.venue_order_id,
        "reason": order.reason,
        "history": json.dumps(order.history),
        "updated_at": datetime.now(UTC),
    }


def _dec(value: str) -> Decimal:
    return Decimal(value)


class SqlOrderStore:
    """Every order state change is written before the OMS moves on."""

    def __init__(self, engine: AsyncEngine, *, account_id: str) -> None:
        self._engine = engine
        self.account_id = account_id

    async def save(self, order: ManagedOrder) -> None:
        row = _order_row(order, self.account_id)
        key = orders_table.c.client_order_id == order.client_order_id
        async with self._engine.begin() as connection:
            result = await connection.execute(update(orders_table).where(key).values(**row))
            if result.rowcount == 0:
                await connection.execute(orders_table.insert().values(**row))

    async def load(self) -> list[ManagedOrder]:
        from .manager import ManagedOrder

        async with self._engine.connect() as connection:
            rows = (
                await connection.execute(
                    select(orders_table)
                    .where(orders_table.c.account_id == self.account_id)
                    .order_by(orders_table.c.updated_at, orders_table.c.client_order_id)
                )
            ).mappings()
            return [
                ManagedOrder(
                    intent=TradeIntent.model_validate_json(row["intent"]),
                    client_order_id=row["client_order_id"],
                    status=OrderStatus(row["status"]),
                    reserved_asset=row["reserved_asset"],
                    reserved=_dec(row["reserved"]),
                    released=_dec(row["released"]),
                    filled_quantity=_dec(row["filled_quantity"]),
                    filled_notional=_dec(row["filled_notional"]),
                    fee=_dec(row["fee"]),
                    venue_order_id=row["venue_order_id"],
                    reason=row["reason"],
                    history=json.loads(row["history"]),
                )
                for row in rows
            ]


Checkpoint = tuple[date, Decimal, Decimal]  # (UTC day, start-of-day equity, peak equity)


class InMemoryCheckpoints:
    def __init__(self) -> None:
        self._values: dict[str, Checkpoint] = {}

    async def get(self, quote_asset: str) -> Checkpoint | None:
        return self._values.get(quote_asset)

    async def put(self, quote_asset: str, checkpoint: Checkpoint) -> None:
        self._values[quote_asset] = checkpoint


class SqlCheckpoints:
    """Start-of-day and peak equity per quote currency, so a restart can neither
    reset the daily-loss baseline nor forget the drawdown peak."""

    def __init__(self, engine: AsyncEngine, *, account_id: str) -> None:
        self._engine = engine
        self.account_id = account_id

    def _key(self, quote_asset: str):
        return (equity_checkpoints_table.c.account_id == self.account_id) & (
            equity_checkpoints_table.c.quote_asset == quote_asset
        )

    async def get(self, quote_asset: str) -> Checkpoint | None:
        table = equity_checkpoints_table
        async with self._engine.connect() as connection:
            row = (
                await connection.execute(
                    select(table.c.day, table.c.start_of_day_equity, table.c.peak_equity).where(
                        self._key(quote_asset)
                    )
                )
            ).first()
        if row is None:
            return None
        return row[0], _dec(row[1]), _dec(row[2])

    async def put(self, quote_asset: str, checkpoint: Checkpoint) -> None:
        day, start, peak = checkpoint
        values = {
            "day": day,
            "start_of_day_equity": str(start),
            "peak_equity": str(peak),
            "updated_at": datetime.now(UTC),
        }
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(equity_checkpoints_table).where(self._key(quote_asset)).values(**values)
            )
            if result.rowcount == 0:
                await connection.execute(
                    equity_checkpoints_table.insert().values(
                        account_id=self.account_id, quote_asset=quote_asset, **values
                    )
                )
