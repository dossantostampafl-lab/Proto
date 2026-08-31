from __future__ import annotations

from datetime import datetime
from enum import IntEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReplayPhase(IntEnum):
    MARKET_DATA = 0
    FEATURES = 1
    SIGNAL = 2
    RISK = 3
    ORDER = 4
    FILL = 5
    PORTFOLIO = 6


class ReplayEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    phase: ReplayPhase
    stream: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    event_type: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("replay event timestamp must be timezone-aware")
        return value


class ReplaySession(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str = Field(min_length=1, max_length=128)
    seed: int = Field(default=0, ge=0)
    events: tuple[ReplayEvent, ...]

    @model_validator(mode="after")
    def validate_event_identity_and_sequence(self) -> ReplaySession:
        event_ids: set[str] = set()
        last_sequence_by_stream: dict[str, int] = {}
        for event in sorted(
            self.events,
            key=lambda item: (item.observed_at, item.stream, item.sequence, item.event_id),
        ):
            if event.event_id in event_ids:
                raise ValueError(f"duplicate replay event_id: {event.event_id}")
            event_ids.add(event.event_id)

            previous_sequence = last_sequence_by_stream.get(event.stream)
            if previous_sequence is not None and event.sequence <= previous_sequence:
                raise ValueError(
                    f"non-monotonic sequence for replay stream {event.stream}: "
                    f"{event.sequence} <= {previous_sequence}"
                )
            last_sequence_by_stream[event.stream] = event.sequence
        return self


class ReplayEngine:
    def __init__(self, session: ReplaySession) -> None:
        self.session = session
        self._ordered_events = tuple(
            sorted(
                session.events,
                key=lambda item: (
                    item.observed_at,
                    int(item.phase),
                    item.stream,
                    item.sequence,
                    item.event_id,
                ),
            )
        )

    @property
    def ordered_events(self) -> tuple[ReplayEvent, ...]:
        return self._ordered_events

    def events_visible_at(self, as_of: datetime) -> tuple[ReplayEvent, ...]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("replay visibility timestamp must be timezone-aware")
        return tuple(event for event in self._ordered_events if event.observed_at <= as_of)

    def events_between(self, start: datetime, end: datetime) -> tuple[ReplayEvent, ...]:
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("replay start timestamp must be timezone-aware")
        if end.tzinfo is None or end.utcoffset() is None:
            raise ValueError("replay end timestamp must be timezone-aware")
        if end < start:
            raise ValueError("replay end timestamp must be on or after start")
        return tuple(
            event for event in self._ordered_events if start <= event.observed_at <= end
        )

    def fingerprint(self) -> str:
        canonical = self.session.model_dump_json(exclude_none=False)
        return sha256(canonical.encode("utf-8")).hexdigest()
