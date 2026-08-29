from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class JournalEvent:
    event_id: UUID
    timestamp: datetime
    event_type: str
    source: str
    correlation_id: UUID
    payload: dict[str, Any]
    previous_hash: str
    hash: str


class EventJournal:
    def __init__(self) -> None:
        self._events: list[JournalEvent] = []
        self._event_ids: set[UUID] = set()
        self._idempotency_keys: set[str] = set()

    @staticmethod
    def _hash_payload(
        *,
        event_id: UUID,
        timestamp: datetime,
        event_type: str,
        source: str,
        correlation_id: UUID,
        payload: dict[str, Any],
        previous_hash: str,
    ) -> str:
        canonical = json.dumps(
            {
                "event_id": str(event_id),
                "timestamp": timestamp.astimezone(UTC).isoformat(),
                "type": event_type,
                "source": source,
                "correlation_id": str(correlation_id),
                "payload": payload,
                "previous_hash": previous_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    def append(
        self,
        *,
        event_type: str,
        source: str,
        payload: dict[str, Any],
        correlation_id: UUID | None = None,
        event_id: UUID | None = None,
        idempotency_key: str | None = None,
        timestamp: datetime | None = None,
    ) -> JournalEvent:
        resolved_event_id = event_id or uuid4()
        if resolved_event_id in self._event_ids:
            raise ValueError("duplicate event_id")
        if idempotency_key is not None and idempotency_key in self._idempotency_keys:
            raise ValueError("duplicate idempotency_key")

        resolved_timestamp = timestamp or datetime.now(UTC)
        resolved_correlation_id = correlation_id or uuid4()
        previous_hash = self._events[-1].hash if self._events else "GENESIS"
        digest = self._hash_payload(
            event_id=resolved_event_id,
            timestamp=resolved_timestamp,
            event_type=event_type,
            source=source,
            correlation_id=resolved_correlation_id,
            payload=payload,
            previous_hash=previous_hash,
        )
        event = JournalEvent(
            event_id=resolved_event_id,
            timestamp=resolved_timestamp,
            event_type=event_type,
            source=source,
            correlation_id=resolved_correlation_id,
            payload=dict(payload),
            previous_hash=previous_hash,
            hash=digest,
        )
        self._events.append(event)
        self._event_ids.add(resolved_event_id)
        if idempotency_key is not None:
            self._idempotency_keys.add(idempotency_key)
        return event

    def verify(self) -> bool:
        previous_hash = "GENESIS"
        for event in self._events:
            if event.previous_hash != previous_hash:
                return False
            expected = self._hash_payload(
                event_id=event.event_id,
                timestamp=event.timestamp,
                event_type=event.event_type,
                source=event.source,
                correlation_id=event.correlation_id,
                payload=event.payload,
                previous_hash=event.previous_hash,
            )
            if event.hash != expected:
                return False
            previous_hash = event.hash
        return True

    def snapshot(self) -> list[dict[str, Any]]:
        return [asdict(event) for event in self._events]

    def __len__(self) -> int:
        return len(self._events)
