from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import insert

from services.events import EventJournal, EventRuntime

from .app_state import persistence_engine
from .schema_registry import CANONICAL_TABLES
from .settings import settings

logger = logging.getLogger("proto.events")

event_runtime = EventRuntime(
    backend=settings.event_bus_backend,
    redis_url=settings.redis_url,
)
event_journal = EventJournal()
_persisted_count = 0
_persistence_failures = 0


def _event_payload(event: object) -> dict[str, object]:
    return {
        "event_id": str(event.event_id),
        "timestamp": event.timestamp.isoformat(),
        "event_type": event.event_type,
        "source": event.source,
        "correlation_id": str(event.correlation_id),
        "payload": event.payload,
        "previous_hash": event.previous_hash,
        "hash": event.hash,
    }


async def record_operational_event(
    *,
    event_type: str,
    source: str,
    payload: Mapping[str, Any],
    correlation_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> dict[str, object]:
    """Append, persist and publish one operational event.

    The in-process hash chain is the immediate audit trace. When canonical
    persistence is enabled, the same immutable event is also written to
    ``audit_events``. Event-bus publication is secondary telemetry and is
    deliberately best-effort so an unavailable optional bus cannot mutate the
    already-recorded operational state.
    """
    global _persisted_count, _persistence_failures

    event = event_journal.append(
        event_type=event_type,
        source=source,
        payload=dict(payload),
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )
    serialized = _event_payload(event)

    if persistence_engine is not None:
        table = CANONICAL_TABLES["audit_events"]
        try:
            async with persistence_engine.begin() as connection:
                await connection.execute(
                    insert(table).values(
                        id=str(event.event_id),
                        created_at=event.timestamp,
                        correlation_id=str(event.correlation_id),
                        payload=serialized,
                    )
                )
        except Exception:
            _persistence_failures += 1
            logger.exception("operational event persistence failed")
        else:
            _persisted_count += 1

    await event_runtime.safe_publish(
        "proto.audit",
        {
            "event_id": str(event.event_id),
            "event_type": event.event_type,
            "source": event.source,
            "correlation_id": str(event.correlation_id),
            "timestamp": event.timestamp.isoformat(),
        },
    )
    return serialized


def operational_journal_snapshot(*, limit: int = 100) -> dict[str, object]:
    events = event_journal.snapshot()
    selected = events[-limit:]
    return {
        "count": len(events),
        "returned": len(selected),
        "chain_valid": event_journal.verify(),
        "persistence_enabled": persistence_engine is not None,
        "persisted_count": _persisted_count,
        "persistence_failures": _persistence_failures,
        "events": [_event_payload(event) for event in selected],
    }


__all__ = [
    "event_journal",
    "event_runtime",
    "operational_journal_snapshot",
    "record_operational_event",
]
