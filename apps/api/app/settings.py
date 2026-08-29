from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
