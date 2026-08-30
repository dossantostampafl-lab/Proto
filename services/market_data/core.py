from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MarketTick(BaseModel):
    """Canonical normalized market-data event used across research and live monitoring."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    venue: str = Field(min_length=1, max_length=64)
    symbol: str = Field(min_length=1, max_length=32)
    bid: float
    ask: float
    last: float
    volume: float
    bid_size: float
    ask_size: float
    sequence: int = Field(ge=0)

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("market tick timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("venue", "symbol")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("market tick identifiers must not be blank")
        return normalized

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def validate_market_values(self) -> MarketTick:
        values = (
            self.bid,
            self.ask,
            self.last,
            self.volume,
            self.bid_size,
            self.ask_size,
        )
        if not all(isfinite(value) for value in values):
            raise ValueError("market tick values must be finite")
        if min(self.bid, self.ask, self.last) <= 0:
            raise ValueError("market tick prices must be positive")
        if self.ask < self.bid:
            raise ValueError("market tick ask must be greater than or equal to bid")
        if self.volume < 0 or self.bid_size < 0 or self.ask_size < 0:
            raise ValueError("market tick volume and sizes must be non-negative")
        return self

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


class OrderBookMetrics(BaseModel):
    best_bid: float
    best_ask: float
    mid_price: float
    spread: float
    microprice: float
    depth: float
    imbalance: float
    weighted_imbalance: float
    book_pressure: float


class DataQualityIssue(StrEnum):
    DUPLICATE_SEQUENCE = "DUPLICATE_SEQUENCE"
    OUT_OF_ORDER_SEQUENCE = "OUT_OF_ORDER_SEQUENCE"
    OUT_OF_ORDER_TIMESTAMP = "OUT_OF_ORDER_TIMESTAMP"
    STALE_FEED = "STALE_FEED"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    NAIVE_TIMESTAMP = "NAIVE_TIMESTAMP"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"
    PRICE_JUMP = "PRICE_JUMP"
    INVALID_SPREAD = "INVALID_SPREAD"
    NEGATIVE_SIZE = "NEGATIVE_SIZE"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    NON_POSITIVE_PRICE = "NON_POSITIVE_PRICE"


class DataQualityReport(BaseModel):
    valid: bool
    issues: list[DataQualityIssue]


class DataQualityMonitor:
    def __init__(
        self,
        *,
        stale_after_seconds: float = 5.0,
        max_relative_price_jump: float = 0.20,
        max_future_skew_seconds: float = 1.0,
    ) -> None:
        if not isfinite(stale_after_seconds) or stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive and finite")
        if not isfinite(max_relative_price_jump) or max_relative_price_jump < 0:
            raise ValueError("max_relative_price_jump must be non-negative and finite")
        if not isfinite(max_future_skew_seconds) or max_future_skew_seconds < 0:
            raise ValueError("max_future_skew_seconds must be non-negative and finite")
        self.stale_after_seconds = stale_after_seconds
        self.max_relative_price_jump = max_relative_price_jump
        self.max_future_skew_seconds = max_future_skew_seconds
        self._last_by_key: dict[tuple[str, str], MarketTick] = {}

    def reset(self) -> None:
        self._last_by_key.clear()

    def evaluate(
        self,
        tick: MarketTick,
        *,
        now: datetime | None = None,
    ) -> DataQualityReport:
        issues: list[DataQualityIssue] = []
        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        age_seconds = (current_time - tick.timestamp).total_seconds()
        if age_seconds > self.stale_after_seconds:
            issues.append(DataQualityIssue.STALE_FEED)
        elif age_seconds < -self.max_future_skew_seconds:
            issues.append(DataQualityIssue.FUTURE_TIMESTAMP)

        key = (tick.venue, tick.symbol)
        previous = self._last_by_key.get(key)
        if previous is not None:
            if tick.sequence == previous.sequence:
                issues.append(DataQualityIssue.DUPLICATE_SEQUENCE)
            elif tick.sequence < previous.sequence:
                issues.append(DataQualityIssue.OUT_OF_ORDER_SEQUENCE)

            if tick.timestamp < previous.timestamp:
                issues.append(DataQualityIssue.OUT_OF_ORDER_TIMESTAMP)

            previous_mid = previous.mid
            if previous_mid > 0:
                relative_jump = abs(tick.mid - previous_mid) / previous_mid
                if relative_jump > self.max_relative_price_jump:
                    issues.append(DataQualityIssue.PRICE_JUMP)

        if not issues:
            self._last_by_key[key] = tick

        return DataQualityReport(valid=not issues, issues=issues)


def compute_orderbook_metrics(tick: MarketTick) -> OrderBookMetrics:
    total_depth = tick.bid_size + tick.ask_size
    if total_depth > 0:
        imbalance = (tick.bid_size - tick.ask_size) / total_depth
        microprice = (
            tick.ask * tick.bid_size + tick.bid * tick.ask_size
        ) / total_depth
    else:
        imbalance = 0.0
        microprice = tick.mid

    spread = tick.spread
    book_pressure = imbalance * spread
    return OrderBookMetrics(
        best_bid=tick.bid,
        best_ask=tick.ask,
        mid_price=tick.mid,
        spread=spread,
        microprice=microprice,
        depth=total_depth,
        imbalance=imbalance,
        weighted_imbalance=imbalance,
        book_pressure=book_pressure,
    )
