"""Append-only, hash-chained operational journal.

Every event carries a gap-free ``sequence`` and the hash of its predecessor, so
any edit, deletion, reordering or insertion breaks verification from GENESIS.
Payloads are stored as canonical JSON: what is hashed is exactly what is kept,
and a reloaded event hashes to the same value.

This module is the pure, in-memory core. It holds the chain head, a bounded
window of recent events and a bounded idempotency cache; durable storage and
full-history verification live in ``journal_store``.
"""

from __future__ import annotations

import json
from collections import OrderedDict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

GENESIS_HASH = "GENESIS"
HASH_VERSION = "v2"
DEFAULT_WINDOW = 10_000
DEFAULT_KEY_CACHE = 100_000


class JournalError(ValueError):
    pass


class DuplicateEvent(JournalError):
    """The idempotency key or event id was already recorded; ``existing`` is the original."""

    def __init__(self, message: str, existing: JournalEvent | None = None) -> None:
        super().__init__(message)
        self.existing = existing


class JournalConflict(JournalError):
    """The event does not extend the current head (stale or concurrent writer)."""


class JournalCorrupted(JournalError):
    def __init__(self, message: str, sequence: int) -> None:
        super().__init__(f"{message} at sequence {sequence}")
        self.sequence = sequence


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise JournalError("journal payloads must be finite, JSON-native values") from exc


def canonical_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise JournalError("journal timestamps must carry a timezone")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class JournalEvent:
    sequence: int
    event_id: UUID
    timestamp: str
    event_type: str
    source: str
    correlation_id: UUID
    idempotency_key: str | None
    payload_json: str
    previous_hash: str
    hash: str

    @property
    def payload(self) -> dict[str, Any]:
        # A fresh copy each time: callers cannot mutate what was hashed.
        return json.loads(self.payload_json)

    def expected_hash(self) -> str:
        return compute_hash(
            sequence=self.sequence,
            event_id=self.event_id,
            timestamp=self.timestamp,
            event_type=self.event_type,
            source=self.source,
            correlation_id=self.correlation_id,
            idempotency_key=self.idempotency_key,
            payload_json=self.payload_json,
            previous_hash=self.previous_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event_id": str(self.event_id),
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "source": self.source,
            "correlation_id": str(self.correlation_id),
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
            "previous_hash": self.previous_hash,
            "hash": self.hash,
        }


def compute_hash(
    *,
    sequence: int,
    event_id: UUID,
    timestamp: str,
    event_type: str,
    source: str,
    correlation_id: UUID,
    idempotency_key: str | None,
    payload_json: str,
    previous_hash: str,
) -> str:
    material = canonical_json(
        {
            "v": HASH_VERSION,
            "sequence": sequence,
            "event_id": str(event_id),
            "timestamp": timestamp,
            "type": event_type,
            "source": source,
            "correlation_id": str(correlation_id),
            "idempotency_key": idempotency_key,
            "payload": payload_json,
            "previous_hash": previous_hash,
        }
    )
    return sha256(material.encode("utf-8")).hexdigest()


def _check_link(event: JournalEvent, *, sequence: int, previous_hash: str) -> None:
    if event.sequence != sequence:
        raise JournalCorrupted("sequence gap or reorder", event.sequence)
    if event.previous_hash != previous_hash:
        raise JournalCorrupted("broken previous_hash link", event.sequence)
    if event.hash != event.expected_hash():
        raise JournalCorrupted("hash does not match content", event.sequence)


def verify_chain(events: Iterable[JournalEvent]) -> int:
    """Verify a full history from GENESIS; return its length or raise JournalCorrupted."""

    head_sequence, head_hash = 0, GENESIS_HASH
    for event in events:
        _check_link(event, sequence=head_sequence + 1, previous_hash=head_hash)
        head_sequence, head_hash = event.sequence, event.hash
    return head_sequence


class _LruCache(OrderedDict[Any, JournalEvent]):
    def __init__(self, capacity: int) -> None:
        super().__init__()
        self.capacity = capacity

    def remember(self, key: Any, event: JournalEvent) -> None:
        self[key] = event
        self.move_to_end(key)
        if len(self) > self.capacity:
            self.popitem(last=False)


class EventJournal:
    """In-memory chain head plus bounded recent window and idempotency cache.

    Without a durable store, duplicate detection covers the last ``key_cache``
    keys and ids; with a store, its unique constraints are the authority.
    """

    def __init__(self, *, window: int = DEFAULT_WINDOW, key_cache: int = DEFAULT_KEY_CACHE) -> None:
        if window < 1 or key_cache < 1:
            raise ValueError("window and key_cache must be positive")
        self._window: deque[JournalEvent] = deque(maxlen=window)
        self._keys = _LruCache(key_cache)
        self._ids = _LruCache(key_cache)
        self._head_sequence = 0
        self._head_hash = GENESIS_HASH

    @property
    def head_sequence(self) -> int:
        return self._head_sequence

    @property
    def head_hash(self) -> str:
        return self._head_hash

    def __len__(self) -> int:
        return self._head_sequence

    def prepare(
        self,
        *,
        event_type: str,
        source: str,
        payload: Mapping[str, Any],
        correlation_id: UUID | None = None,
        event_id: UUID | None = None,
        idempotency_key: str | None = None,
        timestamp: datetime | None = None,
    ) -> JournalEvent:
        """Build the next event on the current head without recording it."""

        if not event_type or not source:
            raise JournalError("event_type and source are required")
        if idempotency_key is not None:
            existing = self._keys.get(idempotency_key)
            if existing is not None:
                raise DuplicateEvent("duplicate idempotency_key", existing)
        resolved_id = event_id or uuid4()
        if resolved_id in self._ids:
            raise DuplicateEvent("duplicate event_id", self._ids[resolved_id])

        values = {
            "sequence": self._head_sequence + 1,
            "event_id": resolved_id,
            "timestamp": canonical_timestamp(timestamp or datetime.now(UTC)),
            "event_type": event_type,
            "source": source,
            "correlation_id": correlation_id or uuid4(),
            "idempotency_key": idempotency_key,
            "payload_json": canonical_json(dict(payload)),
            "previous_hash": self._head_hash,
        }
        return JournalEvent(**values, hash=compute_hash(**values))

    def commit(self, event: JournalEvent) -> JournalEvent:
        """Record an event prepared on the current head (after it was made durable)."""

        if event.sequence != self._head_sequence + 1 or event.previous_hash != self._head_hash:
            raise JournalConflict("event does not extend the current head")
        if event.hash != event.expected_hash():
            raise JournalCorrupted("hash does not match content", event.sequence)
        self._advance(event)
        return event

    def append(self, **kwargs: Any) -> JournalEvent:
        return self.commit(self.prepare(**kwargs))

    def reset(self) -> None:
        self._window.clear()
        self._keys.clear()
        self._ids.clear()
        self._head_sequence = 0
        self._head_hash = GENESIS_HASH

    def restore(self, event: JournalEvent) -> None:
        """Replay one persisted event during startup, verifying its link to the head."""

        _check_link(event, sequence=self._head_sequence + 1, previous_hash=self._head_hash)
        self._advance(event)

    def remember(self, event: JournalEvent) -> None:
        """Cache an event the durable store already holds (duplicate found there)."""

        if event.idempotency_key is not None:
            self._keys.remember(event.idempotency_key, event)
        self._ids.remember(event.event_id, event)

    def recent(self, limit: int) -> list[JournalEvent]:
        if limit < 1:
            return []
        return list(self._window)[-limit:]

    def verify_window(self) -> bool:
        """Verify the in-memory window; anchored at its first event's predecessor."""

        events = list(self._window)
        if not events:
            return self._head_sequence == 0 and self._head_hash == GENESIS_HASH
        try:
            sequence, previous = events[0].sequence, events[0].previous_hash
            for event in events:
                _check_link(event, sequence=sequence, previous_hash=previous)
                sequence, previous = event.sequence + 1, event.hash
        except JournalCorrupted:
            return False
        return events[-1].sequence == self._head_sequence and previous == self._head_hash

    def _advance(self, event: JournalEvent) -> None:
        self._window.append(event)
        self.remember(event)
        self._head_sequence = event.sequence
        self._head_hash = event.hash
