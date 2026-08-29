from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .safety_policy import SafetyPolicyError, validate_sandbox_mode


class Settings(BaseSettings):
    app_env: str = "development"
    system_mode: str = "SIMULATION"
    database_url: str = "sqlite+aiosqlite:///:memory:"
    redis_url: str = "redis://localhost:6379/0"
    event_bus_backend: str = "memory"
    persistence_enabled: bool = False
    minimum_net_edge: float = 0.01
    minimum_confidence: float = 0.55
    max_position: float = 10.0
    max_notional: float = 100_000.0
    max_daily_drawdown: float = 5_000.0

    @field_validator("system_mode")
    @classmethod
    def enforce_sandbox_mode(cls, value: str) -> str:
        try:
            validate_sandbox_mode(value)
        except SafetyPolicyError as error:
            raise ValueError(str(error)) from error
        return value.strip().upper()

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
