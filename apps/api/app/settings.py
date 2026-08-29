from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .safety_policy import SafetyPolicyError, validate_runtime_mode


class Settings(BaseSettings):
    app_env: str = "development"
    system_mode: str = "LIVE_MONITORING"
    live_monitoring_autostart: bool = False
    live_history_retention_seconds: int = Field(default=86_400, ge=300, le=604_800)
    live_history_query_max: int = Field(default=1_000, ge=1, le=10_000)
    live_history_prune_every_writes: int = Field(default=1_000, ge=1, le=100_000)
    database_url: str = "sqlite+aiosqlite:///:memory:"
    redis_url: str = "redis://localhost:6379/0"
    event_bus_backend: str = "memory"
    persistence_enabled: bool = False
    http_rate_limit_per_minute: int = Field(default=600, ge=1, le=100_000)
    minimum_net_edge: float = 0.01
    minimum_confidence: float = 0.55
    max_position: float = 10.0
    max_notional: float = 100_000.0
    max_daily_drawdown: float = 5_000.0

    @field_validator("system_mode")
    @classmethod
    def enforce_runtime_mode(cls, value: str) -> str:
        try:
            validate_runtime_mode(value)
        except SafetyPolicyError as error:
            raise ValueError(str(error)) from error
        return value.strip().upper()

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
