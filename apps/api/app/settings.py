from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .safety_policy import SafetyPolicyError, validate_runtime_mode


class Settings(BaseSettings):
    app_env: str = "development"
    system_mode: str = "LIVE_MONITORING"
    synthetic_research_enabled: bool = False
    live_monitoring_autostart: bool = False
    live_market_source: str = "COINBASE"
    live_persistence_enabled: bool = False
    live_history_retention_seconds: int = Field(default=86_400, ge=300, le=604_800)
    live_history_query_max: int = Field(default=1_000, ge=1, le=10_000)
    live_history_prune_every_writes: int = Field(default=1_000, ge=1, le=100_000)
    live_database_auto_create: bool = True
    database_url: str = "sqlite+aiosqlite:///:memory:"
    redis_url: str = "redis://localhost:6379/0"
    event_bus_backend: str = "memory"
    persistence_enabled: bool = False
    orchestration_persistence_enabled: bool = False
    http_rate_limit_per_minute: int = Field(default=600, ge=1, le=100_000)
    minimum_net_edge: float = Field(default=0.01, ge=0.0, le=1.0, allow_inf_nan=False)
    minimum_confidence: float = Field(default=0.55, ge=0.0, le=1.0, allow_inf_nan=False)
    max_position: float = Field(default=10.0, gt=0.0, allow_inf_nan=False)
    max_notional: float = Field(default=100_000.0, gt=0.0, allow_inf_nan=False)
    max_daily_drawdown: float = Field(default=5_000.0, gt=0.0, allow_inf_nan=False)
    simulation_max_order_notional: float = Field(
        default=10_000.0,
        gt=0.0,
        allow_inf_nan=False,
    )
    simulation_max_position_notional: float = Field(
        default=25_000.0,
        gt=0.0,
        allow_inf_nan=False,
    )
    simulation_max_slippage_bps: float = Field(
        default=75.0,
        ge=0.0,
        allow_inf_nan=False,
    )
    simulation_max_gross_exposure: float = Field(
        default=75_000.0,
        gt=0.0,
        allow_inf_nan=False,
    )
    simulation_max_asset_concentration: float = Field(
        default=1.0,
        gt=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    simulation_max_volatility: float = Field(
        default=1.5,
        gt=0.0,
        allow_inf_nan=False,
    )
    simulation_max_order_to_book_ratio: float = Field(
        default=0.50,
        gt=0.0,
        le=1.0,
        allow_inf_nan=False,
    )

    @field_validator("system_mode")
    @classmethod
    def enforce_runtime_mode(cls, value: str) -> str:
        try:
            validate_runtime_mode(value)
        except SafetyPolicyError as error:
            raise ValueError(str(error)) from error
        return value.strip().upper()

    @field_validator("live_market_source")
    @classmethod
    def enforce_live_market_source(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"COINBASE", "BINANCE"}:
            raise ValueError("live_market_source must be COINBASE or BINANCE")
        return normalized

    @property
    def orchestration_persistence_active(self) -> bool:
        """Resolve durable orchestration storage without enabling simulation persistence.

        Railway's existing production service already persists public live history in
        PostgreSQL. Some deployments inject that configuration at the platform layer
        rather than through the repository Dockerfile, so the dedicated orchestration
        flag may be absent even though a durable database is already explicitly in use.
        In that case it is safe to reuse the same durable database for read-only
        orchestration/decision-memory state. General simulation persistence remains
        controlled exclusively by ``persistence_enabled``.
        """
        if self.orchestration_persistence_enabled:
            return True
        durable_database = self.database_url.strip().lower().startswith("postgresql")
        return self.live_persistence_enabled and durable_database

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
