from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from services.events import (
    DuplicateEvent,
    EventJournal,
    EventRuntime,
    JournalConflict,
    JournalCorrupted,
    JournalEvent,
    SqlJournalStore,
)

from .app_state import persistence_engine
from .settings import settings

logger = logging.getLogger("proto.events")

event_runtime = EventRuntime(
    backend=settings.event_bus_backend,
    redis_url=settings.redis_url,
)


class JournalIntegrity(StrEnum):
    UNVERIFIED = "UNVERIFIED"  # durable store configured but history not loaded yet
    VALID = "VALID"
    CORRUPTED = "CORRUPTED"  # writes refused until an operator investigates


class JournalUnavailable(RuntimeError):
    """The event could not be made durable; nothing was recorded."""


@dataclass(frozen=True)
class RecordResult:
    event: dict[str, Any]
    created: bool


class OperationalJournal:
    """Durable-first operational audit journal.

    With a store, every event is written to the database before the in-memory
    head advances, so memory never holds an event the database lacks. On
    startup the full history is streamed back and verified from GENESIS; a
    broken chain puts the journal in CORRUPTED state and refuses new writes.
    """

    def __init__(self, store: SqlJournalStore | None, journal: EventJournal | None = None) -> None:
        self.store = store
        self.journal = journal or EventJournal()
        self.integrity = JournalIntegrity.UNVERIFIED if store else JournalIntegrity.VALID
        self.corruption: str | None = None
        self.write_failures = 0
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self.store is None:
            return
        async with self._lock:
            self.journal.reset()
            try:
                async for event in self.store.iter_events():
                    self.journal.restore(event)
            except JournalCorrupted as exc:
                self._mark_corrupted(str(exc))
                return
            self.integrity = JournalIntegrity.VALID
            self.corruption = None
            logger.info(
                "operational journal restored: %s events, head %s",
                self.journal.head_sequence,
                self.journal.head_hash,
            )

    async def record(
        self,
        *,
        event_type: str,
        source: str,
        payload: Mapping[str, Any],
        correlation_id: UUID | None = None,
        idempotency_key: str | None = None,
        timestamp: datetime | None = None,
    ) -> RecordResult:
        if self.integrity is not JournalIntegrity.VALID:
            raise JournalUnavailable(f"operational journal is {self.integrity.value}")
        async with self._lock:
            try:
                event = self.journal.prepare(
                    event_type=event_type,
                    source=source,
                    payload=payload,
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                    timestamp=timestamp,
                )
            except DuplicateEvent as duplicate:
                assert duplicate.existing is not None
                return RecordResult(duplicate.existing.to_dict(), created=False)

            if self.store is not None:
                try:
                    await self.store.append(event)
                except DuplicateEvent as duplicate:
                    assert duplicate.existing is not None
                    self.journal.remember(duplicate.existing)
                    return RecordResult(duplicate.existing.to_dict(), created=False)
                except JournalConflict as exc:
                    self.write_failures += 1
                    self._mark_corrupted(str(exc))
                    raise JournalUnavailable(str(exc)) from exc
                except Exception as exc:
                    self.write_failures += 1
                    logger.exception("operational journal write failed")
                    raise JournalUnavailable("operational journal write failed") from exc
            self.journal.commit(event)

        await event_runtime.safe_publish(
            "proto.audit",
            {
                "sequence": event.sequence,
                "event_id": str(event.event_id),
                "event_type": event.event_type,
                "source": event.source,
                "correlation_id": str(event.correlation_id),
                "timestamp": event.timestamp,
            },
        )
        return RecordResult(event.to_dict(), created=True)

    async def verify(self) -> dict[str, object]:
        """Full verification: the durable history from GENESIS, else the memory window."""

        if self.store is None:
            valid = self.journal.verify_window()
            return self._verification(valid, scope="memory_window")
        verifier = EventJournal(window=1, key_cache=1)
        try:
            async for event in self.store.iter_events():
                verifier.restore(event)
        except JournalCorrupted as exc:
            return self._verification(False, scope="durable_history", error=str(exc))
        matches_head = (
            verifier.head_sequence == self.journal.head_sequence
            and verifier.head_hash == self.journal.head_hash
        )
        error = None if matches_head else "durable head differs from in-memory head"
        return self._verification(matches_head, scope="durable_history", error=error)

    def snapshot(self, *, limit: int = 100) -> dict[str, object]:
        selected = self.journal.recent(limit)
        return {
            "count": self.journal.head_sequence,
            "returned": len(selected),
            "head_sequence": self.journal.head_sequence,
            "head_hash": self.journal.head_hash,
            "integrity": self.integrity.value,
            "chain_valid": self.integrity is JournalIntegrity.VALID
            and self.journal.verify_window(),
            "corruption": self.corruption,
            "persistence_enabled": self.store is not None,
            "write_failures": self.write_failures,
            "events": [event.to_dict() for event in selected],
        }

    def _verification(
        self, valid: bool, *, scope: str, error: str | None = None
    ) -> dict[str, object]:
        return {
            "status": "valid" if valid else "invalid",
            "chain_valid": valid,
            "scope": scope,
            "error": error,
            "event_count": self.journal.head_sequence,
            "head_hash": self.journal.head_hash,
            "integrity": self.integrity.value,
            "persistence_enabled": self.store is not None,
            "write_failures": self.write_failures,
        }

    def _mark_corrupted(self, reason: str) -> None:
        self.integrity = JournalIntegrity.CORRUPTED
        self.corruption = reason
        logger.critical("operational journal integrity failure: %s", reason)


operational_journal = OperationalJournal(
    SqlJournalStore(persistence_engine) if persistence_engine is not None else None
)


async def record_operational_event(
    *,
    event_type: str,
    source: str,
    payload: Mapping[str, Any],
    correlation_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> RecordResult:
    """Append one operational event; raises JournalUnavailable if it cannot be made durable."""

    result = await operational_journal.record(
        event_type=event_type,
        source=source,
        payload=payload,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )
    if result.created:
        from .floor_surface import publish

        await publish(result.event)
    return result


def operational_journal_snapshot(*, limit: int = 100) -> dict[str, object]:
    return operational_journal.snapshot(limit=limit)


__all__ = [
    "JournalEvent",
    "JournalIntegrity",
    "JournalUnavailable",
    "OperationalJournal",
    "RecordResult",
    "event_runtime",
    "operational_journal",
    "operational_journal_snapshot",
    "record_operational_event",
]
