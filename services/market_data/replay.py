from __future__ import annotations

from collections import Counter
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from .models import BinaryContractSnapshot, Candle, OrderBookSnapshot


class MarketDataEventType(StrEnum):
    ORDER_BOOK = "ORDER_BOOK"
    CANDLE = "CANDLE"
    BINARY_CONTRACT = "BINARY_CONTRACT"


class MarketDataEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    sequence: int = Field(ge=0)
    event_type: MarketDataEventType
    observed_at: datetime
    data: OrderBookSnapshot | Candle | BinaryContractSnapshot

    @model_validator(mode="after")
    def validate_event_payload(self) -> MarketDataEvent:
        expected_type = {
            OrderBookSnapshot: MarketDataEventType.ORDER_BOOK,
            Candle: MarketDataEventType.CANDLE,
            BinaryContractSnapshot: MarketDataEventType.BINARY_CONTRACT,
        }[type(self.data)]
        if self.event_type != expected_type:
            raise ValueError("event_type must match data payload type")
        return self


class ReplayBatch(BaseModel):
    events: list[MarketDataEvent] = Field(min_length=1, max_length=50_000)

    @model_validator(mode="after")
    def validate_ordering(self) -> ReplayBatch:
        sequences = [event.sequence for event in self.events]
        if len(sequences) != len(set(sequences)):
            raise ValueError("event sequences must be unique")
        if sequences != sorted(sequences):
            raise ValueError("events must be sorted by sequence")

        timestamps = [event.observed_at for event in self.events]
        if timestamps != sorted(timestamps):
            raise ValueError("event timestamps must be nondecreasing")
        return self


class ReplaySummary(BaseModel):
    count: int
    first_sequence: int
    last_sequence: int
    started_at: datetime
    ended_at: datetime
    event_counts: dict[str, int]
    sequence_gaps: list[int]


def summarize_replay(batch: ReplayBatch) -> ReplaySummary:
    events = batch.events
    event_counts = Counter(event.event_type.value for event in events)
    gaps = [
        current.sequence - previous.sequence - 1
        for previous, current in zip(events, events[1:], strict=False)
        if current.sequence - previous.sequence > 1
    ]

    return ReplaySummary(
        count=len(events),
        first_sequence=events[0].sequence,
        last_sequence=events[-1].sequence,
        started_at=events[0].observed_at,
        ended_at=events[-1].observed_at,
        event_counts=dict(sorted(event_counts.items())),
        sequence_gaps=gaps,
    )


def select_replay_window(
    batch: ReplayBatch,
    *,
    after_sequence: int = -1,
    limit: int = 100,
) -> list[MarketDataEvent]:
    safe_limit = min(max(limit, 1), 1_000)
    return [event for event in batch.events if event.sequence > after_sequence][:safe_limit]
