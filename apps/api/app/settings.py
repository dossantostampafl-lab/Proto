from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    system_mode: Literal["SIMULATION", "PAPER_TRADING", "HISTORICAL_REPLAY"] = "SIMULATION"
    database_url: str = "sqlite+aiosqlite:///:memory:"
    persistence_enabled: bool = False
    minimum_net_edge: float = Field(default=0.01, ge=0.0, le=1.0, allow_inf_nan=False)
    minimum_confidence: float = Field(default=0.55, ge=0.0, le=1.0, allow_inf_nan=False)
    max_position: float = Field(default=10.0, gt=0.0, allow_inf_nan=False)
    max_notional: float = Field(default=100_000.0, gt=0.0, allow_inf_nan=False)
    max_daily_drawdown: float = Field(default=5_000.0, gt=0.0, allow_inf_nan=False)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
