from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SystemMode(StrEnum):
    SIMULATION = "SIMULATION"
    PAPER_TRADING = "PAPER_TRADING"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"


class KillSwitchState(StrEnum):
    ARMED = "ARMED"
    TRIGGERED = "TRIGGERED"
    LOCKED = "LOCKED"
    RESET_PENDING = "RESET_PENDING"


class RuntimeState(BaseModel):
    mode: SystemMode = SystemMode.SIMULATION
    running: bool = True
    kill_switch: KillSwitchState = KillSwitchState.ARMED
    replay_speed: int = Field(default=1, ge=1, le=100)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Asset(StrEnum):
    BTC = "BTC"
    ETH = "ETH"
    SOL = "SOL"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class MarketSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    symbol: Literal["BTC", "ETH", "SOL"]
    market_id: str = Field(default="research-market", min_length=1, max_length=120)
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    bid_size: float = Field(default=1.0, ge=0)
    ask_size: float = Field(default=1.0, ge=0)
    volatility: float = Field(default=0.2, ge=0)
    imbalance: float = Field(default=0.0, ge=-1.0, le=1.0)
    market_probability: float = Field(default=0.5, ge=0.0, le=1.0)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_book(self) -> MarketSnapshot:
        if self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid")
        return self


class SimulationOrder(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    id: UUID = Field(default_factory=uuid4)
    market_id: str = Field(min_length=1, max_length=120)
    asset: Asset
    side: Side
    quantity: float = Field(gt=0)
    limit_price: float = Field(gt=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Fill(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    order_id: UUID
    market_id: str = Field(min_length=1, max_length=120)
    asset: Asset
    side: Side
    filled_quantity: float = Field(gt=0)
    fill_price: float = Field(gt=0)
    fee: float = Field(ge=0)
    slippage_bps: float = Field(ge=0)
    filled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RiskLimits(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    max_order_notional: float = Field(default=10_000, gt=0)
    max_position_notional: float = Field(default=25_000, gt=0)
    max_slippage_bps: float = Field(default=75, ge=0)


class SimulationRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    order: SimulationOrder
    snapshot: MarketSnapshot
    current_position_notional: float = Field(default=0, ge=0)
    limits: RiskLimits = Field(default_factory=RiskLimits)

    @model_validator(mode="after")
    def validate_asset_symbol(self) -> SimulationRequest:
        if self.order.asset.value != self.snapshot.symbol:
            raise ValueError("order asset must match market snapshot symbol")
        return self


class SimulationResult(BaseModel):
    mode: SystemMode = SystemMode.SIMULATION
    accepted: bool
    reason: str
    fill: Fill | None = None


class MarkPrice(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    asset: Asset
    price: float = Field(gt=0)


class PortfolioMarkRequest(BaseModel):
    marks: list[MarkPrice] = Field(min_length=1)
