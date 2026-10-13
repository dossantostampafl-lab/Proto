"""Durable storage for the operational journal (table ``operational_journal``).

The database is the source of truth: an event is written here before the
in-memory head advances, the sequence is the primary key, and event ids,
idempotency keys and hashes are unique. One API process is the single writer;
a second writer surfaces as a ``JournalConflict`` instead of a forked chain.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
    insert,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from .journal import DuplicateEvent, JournalConflict, JournalEvent

journal_metadata = MetaData()

journal_table = Table(
    "operational_journal",
    journal_metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=False),
    Column("event_id", String(36), nullable=False, unique=True),
    Column("timestamp", String(40), nullable=False),
    Column("event_type", String(120), nullable=False, index=True),
    Column("source", String(120), nullable=False),
    Column("correlation_id", String(36), nullable=False, index=True),
    Column("idempotency_key", String(200), nullable=True, unique=True),
    Column("payload_json", Text, nullable=False),
    Column("previous_hash", String(64), nullable=False, unique=True),
    Column("hash", String(64), nullable=False, unique=True),
)


def _from_row(row) -> JournalEvent:
    return JournalEvent(
        sequence=row.sequence,
        event_id=UUID(row.event_id),
        timestamp=row.timestamp,
        event_type=row.event_type,
        source=row.source,
        correlation_id=UUID(row.correlation_id),
        idempotency_key=row.idempotency_key,
        payload_json=row.payload_json,
        previous_hash=row.previous_hash,
        hash=row.hash,
    )


class SqlJournalStore:
    def __init__(self, engine: AsyncEngine, *, batch_size: int = 1_000) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.engine = engine
        self._batch_size = batch_size

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(journal_metadata.create_all)

    async def append(self, event: JournalEvent) -> None:
        try:
            async with self.engine.begin() as connection:
                await connection.execute(
                    insert(journal_table).values(
                        sequence=event.sequence,
                        event_id=str(event.event_id),
                        timestamp=event.timestamp,
                        event_type=event.event_type,
                        source=event.source,
                        correlation_id=str(event.correlation_id),
                        idempotency_key=event.idempotency_key,
                        payload_json=event.payload_json,
                        previous_hash=event.previous_hash,
                        hash=event.hash,
                    )
                )
        except IntegrityError as exc:
            if event.idempotency_key is not None:
                existing = await self.by_idempotency_key(event.idempotency_key)
                if existing is not None:
                    raise DuplicateEvent("duplicate idempotency_key", existing) from exc
            raise JournalConflict("persisted head moved; another writer is active") from exc

    async def by_idempotency_key(self, key: str) -> JournalEvent | None:
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    select(journal_table).where(journal_table.c.idempotency_key == key)
                )
            ).first()
        return _from_row(row) if row is not None else None

    async def count(self) -> int:
        async with self.engine.connect() as connection:
            return int(
                await connection.scalar(select(func.count()).select_from(journal_table)) or 0
            )

    async def iter_events(self) -> AsyncIterator[JournalEvent]:
        """Stream the whole history in sequence order, in bounded batches."""

        after = 0
        while True:
            async with self.engine.connect() as connection:
                rows = (
                    await connection.execute(
                        select(journal_table)
                        .where(journal_table.c.sequence > after)
                        .order_by(journal_table.c.sequence)
                        .limit(self._batch_size)
                    )
                ).all()
            if not rows:
                return
            for row in rows:
                yield _from_row(row)
            after = rows[-1].sequence
