"""Watchdog configuration (env prefix WATCHDOG_). Read only by the watchdog process."""

from __future__ import annotations

import math
from decimal import Decimal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MIN_SECRET = 32


class WatchdogSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WATCHDOG_", extra="ignore", hide_input_in_errors=True
    )

    id: str = Field(default="watchdog-1", pattern=r"^[A-Za-z0-9_.-]{1,40}$")
    api_url: str
    # Same value as the API's WATCHDOG_API_TOKEN: it can halt and report a heartbeat, nothing else.
    api_token: SecretStr
    executor_url: str | None = None
    # Same value as the executor's EXECUTOR_WATCHDOG_TOKEN; never the executor's API token.
    executor_token: SecretStr | None = None
    # The XP (B3) node on the operator's PC, over Tailscale (docs/runbooks/xp-node-on-pc.md),
    # and that node's EXECUTOR_WATCHDOG_TOKEN. Optional: without it the node is not watched.
    xp_executor_url: str | None = None
    xp_executor_token: SecretStr | None = None
    # The Alpaca (US equities, paper) executor and its EXECUTOR_WATCHDOG_TOKEN. Optional.
    alpaca_executor_url: str | None = None
    alpaca_executor_token: SecretStr | None = None
    interval_seconds: float = Field(default=5.0, ge=1.0, le=60.0, allow_inf_nan=False)
    # How long the API or the executor may stay unreachable before it counts as an outage. It
    # covers a deploy restart (image pull, migrations, warm-up); a real outage still halts.
    outage_grace_seconds: float = Field(default=180.0, ge=15.0, le=300.0, allow_inf_nan=False)
    # false: the operator turned automatic halts off (until they turn them back on). The watchdog
    # still probes and reports every trip, as "halts": "disabled", and halts nothing.
    halts: bool = True
    # Fractions of the constitutional limits; the watchdog may only tighten them.
    daily_loss_fraction: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    drawdown_fraction: Decimal = Field(default=Decimal("1"), gt=0, le=1)

    @property
    def unreachable_cycles(self) -> int:
        return max(1, math.ceil(self.outage_grace_seconds / self.interval_seconds))

    @field_validator("api_token", "executor_token", "xp_executor_token", "alpaca_executor_token")
    @classmethod
    def long_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < _MIN_SECRET:
            raise ValueError(f"must be at least {_MIN_SECRET} characters")
        return value

    @model_validator(mode="after")
    def executor_pair(self) -> WatchdogSettings:
        if bool(self.executor_url) != (self.executor_token is not None):
            raise ValueError("WATCHDOG_EXECUTOR_URL and WATCHDOG_EXECUTOR_TOKEN go together")
        if bool(self.xp_executor_url) != (self.xp_executor_token is not None):
            raise ValueError("WATCHDOG_XP_EXECUTOR_URL and WATCHDOG_XP_EXECUTOR_TOKEN go together")
        if bool(self.alpaca_executor_url) != (self.alpaca_executor_token is not None):
            raise ValueError(
                "WATCHDOG_ALPACA_EXECUTOR_URL and WATCHDOG_ALPACA_EXECUTOR_TOKEN go together"
            )
        return self
