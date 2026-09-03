from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MarketEventKind(StrEnum):
    QUOTE = "QUOTE"
    TRADE = "TRADE"
    BAR = "BAR"
    STATUS = "STATUS"


class MarketEventProvenance(StrEnum):
    PUBLIC_READ_ONLY = "PUBLIC_READ_ONLY"
    LICENSED_READ_ONLY = "LICENSED_READ_ONLY"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    SYNTHETIC_RESEARCH = "SYNTHETIC_RESEARCH"


class MarketEvent(BaseModel):
    """Provider-neutral event envelope for crypto, equities, ETFs and indexes."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    instrument_id: str = Field(min_length=3, max_length=160)
    kind: MarketEventKind
    observed_at: datetime
    received_at: datetime
    source: str = Field(min_length=1, max_length=120)
    provenance: MarketEventProvenance
    sequence: int | None = Field(default=None, ge=0)
    bid: float | None = Field(default=None, gt=0)
    ask: float | None = Field(default=None, gt=0)
    bid_size: float | None = Field(default=None, ge=0)
    ask_size: float | None = Field(default=None, ge=0)
    last: float | None = Field(default=None, gt=0)
    last_size: float | None = Field(default=None, ge=0)
    volume: float | None = Field(default=None, ge=0)
    quality_flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_event(self) -> MarketEvent:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        if self.received_at < self.observed_at:
            raise ValueError("received_at must not predate observed_at")
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid")
        if self.kind is MarketEventKind.QUOTE and (self.bid is None or self.ask is None):
            raise ValueError("QUOTE events require bid and ask")
        if self.kind is MarketEventKind.TRADE and self.last is None:
            raise ValueError("TRADE events require last")
        if ":" not in self.instrument_id:
            raise ValueError("instrument_id must be namespaced")
        return self

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def source_latency_seconds(self) -> float:
        return (self.received_at - self.observed_at).total_seconds()
