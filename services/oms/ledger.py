"""Transactional ledger: positions, balances, fills, fees and reservations.

Balances are sums of signed `LedgerEntry` rows; nothing is stored as a mutable
total. Per asset:

    total       = OPENING_BALANCE + TRADE + FEE
    reserved    = -(RESERVATION + RELEASE)          (outstanding, >= 0)
    available   = total - reserved                  (= sum of every entry)

A batch is posted atomically. Entry ids are deterministic, so re-posting the
same batch is a no-op. Reservations are guarded (available may not go
negative, releases may not exceed reservations); fills are always recorded
because the venue already executed them - divergence is for reconciliation.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid5

from sqlalchemy import Column, DateTime, MetaData, Numeric, String, Table, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.types import TypeDecorator

from services.execution import LedgerEntry, LedgerEntryKind, VenueId

_ENTRY_NAMESPACE = UUID("6b0c9d1e-3f6a-5c2e-9d41-6a0f3b7e2c11")
_TOTAL_KINDS = frozenset(
    {LedgerEntryKind.OPENING_BALANCE, LedgerEntryKind.TRADE, LedgerEntryKind.FEE}
)


class LedgerRejected(ValueError):
    pass


def entry_id(*parts: object) -> UUID:
    return uuid5(_ENTRY_NAMESPACE, "\x00".join(str(part) for part in parts))


@dataclass(frozen=True)
class AssetBalance:
    asset: str
    total: Decimal
    reserved: Decimal
    opening: Decimal = Decimal("0")  # part of total already at the venue (faucet, deposits)

    @property
    def available(self) -> Decimal:
        return self.total - self.reserved

    def to_dict(self) -> dict[str, str]:
        return {
            "total": str(self.total),
            "reserved": str(self.reserved),
            "available": str(self.available),
            "opening": str(self.opening),
        }


def summarize(entries: Iterable[LedgerEntry]) -> dict[str, AssetBalance]:
    total: dict[str, Decimal] = defaultdict(Decimal)
    reserved: dict[str, Decimal] = defaultdict(Decimal)
    opening: dict[str, Decimal] = defaultdict(Decimal)
    for entry in entries:
        if entry.kind in _TOTAL_KINDS:
            total[entry.asset] += entry.amount
            if entry.kind is LedgerEntryKind.OPENING_BALANCE:
                opening[entry.asset] += entry.amount
        else:
            reserved[entry.asset] -= entry.amount
    assets = sorted(set(total) | set(reserved))
    return {a: AssetBalance(a, total[a], reserved[a], opening[a]) for a in assets}


def _guard(balances: dict[str, AssetBalance], batch: Sequence[LedgerEntry]) -> None:
    touched = {e.asset for e in batch if e.kind not in _TOTAL_KINDS}
    for asset in touched:
        balance = balances.get(asset)
        if balance is None:
            continue
        if balance.reserved < 0:
            raise LedgerRejected(f"release exceeds reservations for {asset}")
        if balance.available < 0:
            raise LedgerRejected(f"insufficient available {asset}")


class InMemoryLedger:
    def __init__(self, *, account_id: str, venue: VenueId) -> None:
        self.account_id = account_id
        self.venue = venue
        self._entries: dict[UUID, LedgerEntry] = {}
        self._lock = asyncio.Lock()

    async def post(self, batch: Sequence[LedgerEntry]) -> bool:
        """Apply the whole batch or nothing. Returns False if it was already applied."""

        async with self._lock:
            known = [e.entry_id in self._entries for e in batch]
            if all(known):
                return False
            if any(known):
                raise LedgerRejected("batch partially overlaps posted entries")
            _guard(summarize([*self._entries.values(), *batch]), batch)
            for entry in batch:
                self._entries[entry.entry_id] = entry
            return True

    async def balances(self) -> dict[str, AssetBalance]:
        return summarize(self._entries.values())

    async def has_entry(self, entry: UUID) -> bool:
        return entry in self._entries

    async def entries(self) -> list[LedgerEntry]:
        return sorted(self._entries.values(), key=lambda e: (e.recorded_at, str(e.entry_id)))


class ExactDecimal(TypeDecorator):
    """NUMERIC(38, 18) on PostgreSQL; exact text on SQLite, whose NUMERIC goes through float.

    A column created before this type (SQLite NUMERIC affinity) still hands back a float: it is
    read as its shortest round-trip text, so 0.3 stays 0.3 instead of 0.299999999999999988.
    """

    impl = Numeric(38, 18)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(64))
        return dialect.type_descriptor(Numeric(38, 18))

    def process_bind_param(self, value, dialect):
        if value is None or dialect.name != "sqlite":
            return value
        return format(Decimal(value), "f")

    def process_result_value(self, value, dialect):
        if value is None or isinstance(value, Decimal):
            return value
        if isinstance(value, float):
            return Decimal(repr(value))
        return Decimal(str(value))


ledger_metadata = MetaData()

ledger_table = Table(
    "ledger_entries",
    ledger_metadata,
    Column("entry_id", String(36), primary_key=True),
    Column("account_id", String(80), nullable=False, index=True),
    Column("venue", String(32), nullable=False),
    Column("asset", String(20), nullable=False, index=True),
    Column("amount", ExactDecimal(), nullable=False),
    Column("kind", String(24), nullable=False),
    Column("reference_id", String(80), nullable=False, index=True),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
)


class SqlLedger:
    """Durable ledger; one batch per database transaction. Single writer per account."""

    def __init__(self, engine: AsyncEngine, *, account_id: str, venue: VenueId) -> None:
        self.engine = engine
        self.account_id = account_id
        self.venue = venue
        self._lock = asyncio.Lock()

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(ledger_metadata.create_all)

    def _scope(self):
        return (ledger_table.c.account_id == self.account_id) & (
            ledger_table.c.venue == self.venue.value
        )

    async def post(self, batch: Sequence[LedgerEntry]) -> bool:
        ids = [str(e.entry_id) for e in batch]
        async with self._lock, self.engine.begin() as connection:
            existing = (
                await connection.execute(
                    select(func.count())
                    .select_from(ledger_table)
                    .where(ledger_table.c.entry_id.in_(ids))
                )
            ).scalar_one()
            if existing == len(ids):
                return False
            if existing:
                raise LedgerRejected("batch partially overlaps posted entries")
            rows = (
                await connection.execute(
                    select(ledger_table.c.asset, ledger_table.c.kind, ledger_table.c.amount)
                    .where(self._scope())
                    .where(ledger_table.c.asset.in_({e.asset for e in batch}))
                )
            ).all()
            current = [_row_entry(asset, kind, amount, self) for asset, kind, amount in rows]
            _guard(summarize([*current, *batch]), batch)
            try:
                await connection.execute(
                    ledger_table.insert(),
                    [
                        {
                            "entry_id": str(e.entry_id),
                            "account_id": e.account_id,
                            "venue": e.venue.value,
                            "asset": e.asset,
                            "amount": e.amount,
                            "kind": e.kind.value,
                            "reference_id": e.reference_id,
                            "recorded_at": e.recorded_at,
                        }
                        for e in batch
                    ],
                )
            except IntegrityError as exc:
                raise LedgerRejected("concurrent ledger write detected") from exc
            return True

    async def balances(self) -> dict[str, AssetBalance]:
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    select(ledger_table.c.asset, ledger_table.c.kind, ledger_table.c.amount).where(
                        self._scope()
                    )
                )
            ).all()
        return summarize(_row_entry(a, k, amt, self) for a, k, amt in rows)

    async def has_entry(self, entry: UUID) -> bool:
        async with self.engine.connect() as connection:
            found = await connection.execute(
                select(ledger_table.c.entry_id).where(ledger_table.c.entry_id == str(entry))
            )
            return found.first() is not None


def _row_entry(asset: str, kind: str, amount, ledger: SqlLedger) -> LedgerEntry:
    # Lightweight reconstruction for summarizing; ids are irrelevant for sums.
    return LedgerEntry.model_construct(
        asset=asset,
        kind=LedgerEntryKind(kind),
        amount=Decimal(str(amount)),
        account_id=ledger.account_id,
        venue=ledger.venue,
    )


def opening_balances(
    balances: dict[str, Decimal], *, account_id: str, venue: VenueId, now: datetime
) -> list[LedgerEntry]:
    """Snapshot of funds already held at the venue (no money movement)."""

    return [
        LedgerEntry(
            entry_id=entry_id(account_id, venue.value, "opening", asset),
            account_id=account_id,
            venue=venue,
            asset=asset,
            amount=amount,
            kind=LedgerEntryKind.OPENING_BALANCE,
            reference_id="opening-balance",
            recorded_at=now,
        )
        for asset, amount in sorted(balances.items())
        if amount > 0
    ]
