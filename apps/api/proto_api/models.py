from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RunMode(StrEnum):
    SIMULATION = "SIMULATION"
    PAPER = "PAPER"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Asset(StrEnum):
    BTC = "BTC"
    ETH = "ETH"
    SOL = "SOL"


class MarketSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: str = Field(min_length=1, max_length=120)
    asset: Asset
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    implied_probability: float | None = Field(default=None, ge=0, le=1)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_book(self) -> MarketSnapshot:
        if self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid")
        return self


class SimulationOrder(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    market_id: str = Field(min_length=1, max_length=120)
    asset: Asset
    side: Side
    quantity: float = Field(gt=0)
    limit_price: float = Field(gt=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Fill(BaseModel):
    order_id: UUID
    filled_quantity: float = Field(gt=0)
    fill_price: float = Field(gt=0)
    fee: float = Field(ge=0)
    slippage_bps: float = Field(ge=0)
    filled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RiskLimits(BaseModel):
    max_order_notional: float = Field(default=10_000, gt=0)
    max_position_notional: float = Field(default=25_000, gt=0)
    max_slippage_bps: float = Field(default=75, ge=0)


class SimulationRequest(BaseModel):
    order: SimulationOrder
    snapshot: MarketSnapshot
    current_position_notional: float = Field(default=0, ge=0)
    limits: RiskLimits = Field(default_factory=RiskLimits)


class SimulationResult(BaseModel):
    mode: RunMode = RunMode.SIMULATION
    accepted: bool
    reason: str
    fill: Fill | None = None


class EdgeRequest(BaseModel):
    market_id: str = Field(min_length=1, max_length=120)
    yes_bid: float = Field(ge=0, le=1)
    yes_ask: float = Field(ge=0, le=1)
    fair_probability: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_binary_book(self) -> EdgeRequest:
        if self.yes_ask < self.yes_bid:
            raise ValueError("yes_ask must be greater than or equal to yes_bid")
        return self


class EdgeEstimate(BaseModel):
    market_id: str
    market_mid_probability: float
    fair_probability: float
    edge: float
    edge_bps: float
    side: str


class MarkPrice(BaseModel):
    asset: Asset
    price: float = Field(gt=0)


class PortfolioMarkRequest(BaseModel):
    marks: list[MarkPrice] = Field(min_length=1)
