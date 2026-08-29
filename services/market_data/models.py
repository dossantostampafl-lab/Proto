from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ResearchAsset = Literal["BTC", "ETH", "SOL"]


class DataSource(StrEnum):
    SIMULATED = "SIMULATED"
    HISTORICAL_FIXTURE = "HISTORICAL_FIXTURE"


class BookLevel(BaseModel):
    model_config = ConfigDict(frozen=True)

    price: float = Field(gt=0)
    size: float = Field(ge=0)


class OrderBookSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: str = Field(min_length=1, max_length=120)
    asset: ResearchAsset
    bids: list[BookLevel] = Field(min_length=1, max_length=1_000)
    asks: list[BookLevel] = Field(min_length=1, max_length=1_000)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: DataSource = DataSource.SIMULATED

    @model_validator(mode="after")
    def validate_book(self) -> OrderBookSnapshot:
        bid_prices = [level.price for level in self.bids]
        ask_prices = [level.price for level in self.asks]
        if bid_prices != sorted(bid_prices, reverse=True):
            raise ValueError("bids must be sorted from highest to lowest price")
        if ask_prices != sorted(ask_prices):
            raise ValueError("asks must be sorted from lowest to highest price")
        if self.bids[0].price > self.asks[0].price:
            raise ValueError("best bid must not exceed best ask")
        return self


class Candle(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: str = Field(min_length=1, max_length=120)
    asset: ResearchAsset
    timeframe: Literal["1s", "5s", "1m", "5m", "1h", "1d"]
    started_at: datetime
    ended_at: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    source: DataSource = DataSource.HISTORICAL_FIXTURE

    @model_validator(mode="after")
    def validate_candle(self) -> Candle:
        if self.ended_at <= self.started_at:
            raise ValueError("ended_at must be after started_at")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be greater than or equal to OHLC values")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be less than or equal to OHLC values")
        return self


class BinaryContractSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: str = Field(min_length=1, max_length=120)
    underlying_asset: ResearchAsset
    yes_bid: float = Field(ge=0, le=1)
    yes_ask: float = Field(ge=0, le=1)
    expires_at: datetime
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: DataSource = DataSource.SIMULATED

    @model_validator(mode="after")
    def validate_contract(self) -> BinaryContractSnapshot:
        if self.yes_ask < self.yes_bid:
            raise ValueError("yes_ask must be greater than or equal to yes_bid")
        if self.expires_at <= self.observed_at:
            raise ValueError("expires_at must be after observed_at")
        return self
