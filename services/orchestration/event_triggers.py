from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from services.events.bus import EventBus

from .runtime import ProtoBrain


class AutonomousEventType(StrEnum):
    MARKET_TICK = "MARKET_TICK"
    REGIME_CHANGED = "REGIME_CHANGED"
    OPPORTUNITY_FOUND = "OPPORTUNITY_FOUND"
    MODEL_DEGRADED = "MODEL_DEGRADED"


@dataclass(frozen=True, slots=True)
class EventTriggerRule:
    event_type: AutonomousEventType
    job_name: str
    mode: str


class AutonomousEventDispatcher:
    """Translate allowlisted bus events into durable ProtoBrain jobs.

    Job names and modes are code-owned rules; an event payload cannot escalate
    itself into another capability. A stream may declare a default semantic event
    type when its schema already fixes the meaning, as with proto.market.normalized.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        brain: ProtoBrain,
        stream: str,
        rules: tuple[EventTriggerRule, ...],
        default_event_type: AutonomousEventType | None = None,
    ) -> None:
        if not stream.strip():
            raise ValueError("event stream must be non-empty")
        self.bus = bus
        self.brain = brain
        self.stream = stream
        self.rules = {rule.event_type.value: rule for rule in rules}
        self.default_event_type = default_event_type
        if default_event_type is not None and default_event_type.value not in self.rules:
            raise ValueError("default event type must have a configured trigger rule")
        self._cursor = "0-0"
        self._processed = 0
        self._ignored = 0
        self._last_error: str | None = None

    def status(self) -> dict[str, object]:
        return {
            "stream": self.stream,
            "cursor": self._cursor,
            "rules": sorted(self.rules),
            "default_event_type": (
                self.default_event_type.value if self.default_event_type is not None else None
            ),
            "processed": self._processed,
            "ignored": self._ignored,
            "last_error": self._last_error,
            "financial_connectivity": False,
            "real_money_execution": False,
        }

    async def drain(self) -> int:
        dispatched = 0
        async for message in self.bus.subscribe(self.stream, after=self._cursor):
            self._cursor = message.message_id
            event_type = message.payload.get("event_type")
            if event_type is None and self.default_event_type is not None:
                event_type = self.default_event_type.value
            rule = self.rules.get(event_type or "")
            if rule is None:
                self._ignored += 1
                continue

            try:
                await self.brain.enqueue(
                    rule.job_name,
                    idempotency_key=(
                        f"event:{self.stream}:{message.message_id}:{rule.job_name}:{rule.mode}"
                    ),
                    mode=rule.mode,
                    payload={
                        "trigger_event": event_type,
                        "trigger_stream": self.stream,
                        "trigger_message_id": message.message_id,
                        "event_id": message.payload.get("event_id"),
                        "symbol": message.payload.get("symbol"),
                        "observed_at": (
                            message.payload.get("occurred_at")
                            or message.payload.get("observed_at")
                        ),
                        "received_at": message.payload.get("received_at"),
                        "sequence": message.payload.get("sequence"),
                        "source": message.payload.get("source"),
                    },
                )
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                raise

            self._processed += 1
            dispatched += 1
            self._last_error = None
        return dispatched
