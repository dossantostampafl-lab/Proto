from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ResearchAsset = Literal["BTC", "ETH", "SOL"]


class DataSource(StrEnum):
    SIMULATED = "SIMULATED"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    PUBLIC_READ_ONLY = "PUBLIC_READ_ONLY"


class BookLevel(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    price: float = Field(gt=0)
    size: float = Field(ge=0)


class OrderBookSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    market_id: str = Field(min_length=1, max_length=120)
    asset: ResearchAsset
    bids: tuple[BookLevel, ...] = Field(min_length=1, max_length=1_000)
    asks: tuple[BookLevel, ...] = Field(min_length=1, max_length=1_000)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: DataSource = DataSource.SIMULATED

    @model_validator(mode="after")
    def validate_book(self) -> OrderBookSnapshot:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        bid_prices = [level.price for level in self.bids]
        ask_prices = [level.price for level in self.asks]
        if bid_prices != sorted(bid_prices, reverse=True):
            raise ValueError("bids must be sorted from highest to lowest price")
        if ask_prices != sorted(ask_prices):
            raise ValueError("asks must be sorted from lowest to highest price")
        if self.bids[0].price > self.asks[0].price:
            raise ValueError("best bid must not exceed best ask")
        return self

    @property
    def mid_price(self) -> float:
        return (self.bids[0].price + self.asks[0].price) / 2.0

    @property
    def spread(self) -> float:
        return self.asks[0].price - self.bids[0].price

    @property
    def total_depth(self) -> float:
        return sum(level.size for level in self.bids) + sum(level.size for level in self.asks)


class Candle(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    market_id: str = Field(min_length=1, max_length=120)
    asset: ResearchAsset
    timeframe: Literal["1s", "5s", "15s", "30s", "1m", "5m", "15m", "1h", "4h", "1d"]
    started_at: datetime
    ended_at: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    source: DataSource = DataSource.HISTORICAL_REPLAY

    @model_validator(mode="after")
    def validate_candle(self) -> Candle:
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        if self.ended_at.tzinfo is None or self.ended_at.utcoffset() is None:
            raise ValueError("ended_at must be timezone-aware")
        if self.ended_at <= self.started_at:
            raise ValueError("ended_at must be after started_at")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be greater than or equal to OHLC values")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be less than or equal to OHLC values")
        return self


class BinaryContractSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    market_id: str = Field(min_length=1, max_length=120)
    underlying_asset: ResearchAsset
    yes_bid: float = Field(ge=0, le=1)
    yes_ask: float = Field(ge=0, le=1)
    expires_at: datetime
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: DataSource = DataSource.SIMULATED

    @model_validator(mode="after")
    def validate_contract(self) -> BinaryContractSnapshot:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        if self.yes_ask < self.yes_bid:
            raise ValueError("yes_ask must be greater than or equal to yes_bid")
        if self.expires_at <= self.observed_at:
            raise ValueError("expires_at must be after observed_at")
        return self

    @property
    def implied_probability(self) -> float:
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def probability_spread(self) -> float:
        return self.yes_ask - self.yes_bid
