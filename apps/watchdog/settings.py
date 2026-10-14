"""Watchdog configuration (env prefix WATCHDOG_). Read only by the watchdog process."""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MIN_SECRET = 32


class WatchdogSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WATCHDOG_", extra="ignore")

    id: str = Field(default="watchdog-1", pattern=r"^[A-Za-z0-9_.-]{1,40}$")
    api_url: str
    # Same value as the API's WATCHDOG_API_TOKEN: it can halt and report a heartbeat, nothing else.
    api_token: SecretStr
    executor_url: str | None = None
    # Same value as the executor's EXECUTOR_WATCHDOG_TOKEN; never the executor's API token.
    executor_token: SecretStr | None = None
    interval_seconds: float = Field(default=5.0, ge=1.0, le=60.0, allow_inf_nan=False)
    # Fractions of the constitutional limits; the watchdog may only tighten them.
    daily_loss_fraction: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    drawdown_fraction: Decimal = Field(default=Decimal("1"), gt=0, le=1)

    @field_validator("api_token", "executor_token")
    @classmethod
    def long_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < _MIN_SECRET:
            raise ValueError(f"must be at least {_MIN_SECRET} characters")
        return value

    @model_validator(mode="after")
    def executor_pair(self) -> WatchdogSettings:
        if bool(self.executor_url) != (self.executor_token is not None):
            raise ValueError("WATCHDOG_EXECUTOR_URL and WATCHDOG_EXECUTOR_TOKEN go together")
        return self
