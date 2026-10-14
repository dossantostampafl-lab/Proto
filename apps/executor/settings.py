"""Executor configuration. Read only by the executor process (env prefix EXECUTOR_)."""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from services.risk.permit_keys import decode_key
from services.venues.credentials import VenueEnvironment

_MIN_SECRET = 32


class ExecutorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EXECUTOR_", extra="ignore")

    # Only the testnet until a live scope is certified (Phase 6).
    environment: VenueEnvironment = VenueEnvironment.TESTNET
    account: str = Field(default="main", pattern=r"^[A-Za-z0-9_-]{1,40}$")
    database_url: str = "sqlite+aiosqlite:///:memory:"
    # Bearer token the public API presents over the private network.
    api_token: SecretStr
    # Verifies Risk Permits. Prefer the Ed25519 public key (the executor then cannot mint
    # permits); the HMAC key is the same value as the API's RISK_PERMIT_SIGNING_KEY.
    permit_public_key: str | None = None
    permit_signing_key: SecretStr | None = None
    symbols: str = "BTCUSDT,ETHUSDT,SOLUSDT"
    poll_seconds: float = Field(default=5.0, ge=1.0, le=300.0, allow_inf_nan=False)
    reconcile_seconds: float = Field(default=60.0, ge=5.0, le=3_600.0, allow_inf_nan=False)

    @field_validator("environment")
    @classmethod
    def testnet_only(cls, value: VenueEnvironment) -> VenueEnvironment:
        if value is not VenueEnvironment.TESTNET:
            raise ValueError("no certified live scope: the executor runs on the testnet only")
        return value

    @field_validator("api_token", "permit_signing_key")
    @classmethod
    def long_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < _MIN_SECRET:
            raise ValueError(f"must be at least {_MIN_SECRET} characters")
        return value

    @model_validator(mode="after")
    def permit_key_present(self) -> ExecutorSettings:
        if self.permit_public_key:
            decode_key(self.permit_public_key)
        elif self.permit_signing_key is None:
            raise ValueError("EXECUTOR_PERMIT_PUBLIC_KEY or EXECUTOR_PERMIT_SIGNING_KEY required")
        return self

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.symbols.split(",") if s.strip()]

    @property
    def account_id(self) -> str:
        return f"binance-spot-{self.environment.value.lower()}-{self.account.lower()}"
