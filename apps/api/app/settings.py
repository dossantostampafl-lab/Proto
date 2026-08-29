from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    system_mode: str = "LIVE_DATA_READ_ONLY"
    database_url: str = "sqlite+aiosqlite:///:memory:"
    persistence_enabled: bool = False
    minimum_net_edge: float = 0.01
    minimum_confidence: float = 0.55
    max_position: float = 10.0
    max_notional: float = 100_000.0
    max_daily_drawdown: float = 5_000.0
    live_data_source: str = "binance"
    live_data_symbol: str = "BTCUSDT"
    live_data_stale_after_seconds: float = 10.0
    live_data_connect_timeout_seconds: float = 10.0
    live_data_max_backoff_seconds: float = 30.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
