"""Executor configuration. Read only by the executor process (env prefix EXECUTOR_)."""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from services.execution import VenueId
from services.risk.permit_keys import decode_key
from services.venues.credentials import VenueEnvironment

_MIN_SECRET = 32


# The three original pairs and the EMA agents' ten extra ones. A symbol the testnet does not list
# is left out at start (load_instruments), so its orders are rejected; the rest still trade.
_BINANCE_DEFAULT_SYMBOLS = (
    "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,LTCUSDT,"
    "DOTUSDT,TRXUSDT,BCHUSDT"
)


class ExecutorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EXECUTOR_", extra="ignore", hide_input_in_errors=True
    )

    # Only the testnet until a live scope is certified (Phase 6).
    environment: VenueEnvironment = VenueEnvironment.TESTNET
    account: str = Field(default="main", pattern=r"^[A-Za-z0-9_-]{1,40}$")
    database_url: str = "sqlite+aiosqlite:///:memory:"
    # Bearer token the public API presents over the private network.
    api_token: SecretStr
    # Phase 6b: the independent watchdog's token. It can read health and halt, nothing else.
    watchdog_token: SecretStr | None = None
    # Verifies Risk Permits. Prefer the Ed25519 public key (the executor then cannot mint
    # permits); the HMAC key is the same value as the API's RISK_PERMIT_SIGNING_KEY.
    permit_public_key: str | None = None
    permit_signing_key: SecretStr | None = None
    # One executor per venue: it holds only that venue's credentials, ledger and halt.
    venue: VenueId = VenueId.BINANCE_SPOT
    # XP_B3 only: the MT5 bridge on the Windows host (private network) and its bearer token.
    bridge_url: str | None = None
    bridge_token: SecretStr | None = None
    # Binance defaults to the three majors; XP_B3 and ALPACA_US have no default (e.g. SPY,QQQ).
    symbols: str | None = None
    poll_seconds: float = Field(default=5.0, ge=1.0, le=300.0, allow_inf_nan=False)
    reconcile_seconds: float = Field(default=60.0, ge=5.0, le=3_600.0, allow_inf_nan=False)

    @field_validator("environment")
    @classmethod
    def testnet_only(cls, value: VenueEnvironment) -> VenueEnvironment:
        if value is not VenueEnvironment.TESTNET:
            raise ValueError("no certified live scope: the executor runs on the testnet only")
        return value

    @field_validator("venue")
    @classmethod
    def executable_venue(cls, value: VenueId) -> VenueId:
        if value not in (VenueId.BINANCE_SPOT, VenueId.XP_B3, VenueId.ALPACA_US):
            raise ValueError("the executor serves BINANCE_SPOT, XP_B3 or ALPACA_US only")
        return value

    @field_validator("api_token", "permit_signing_key", "watchdog_token", "bridge_token")
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

    @model_validator(mode="after")
    def venue_settings_are_complete(self) -> ExecutorSettings:
        if self.venue is VenueId.XP_B3:
            if not self.bridge_url or self.bridge_token is None:
                raise ValueError("EXECUTOR_BRIDGE_URL and EXECUTOR_BRIDGE_TOKEN required for XP_B3")
            if not self.symbol_list:
                raise ValueError("EXECUTOR_SYMBOLS required for XP_B3 (e.g. PETR4F,VALE3F)")
            if self.bridge_token.get_secret_value() == self.api_token.get_secret_value():
                raise ValueError("EXECUTOR_BRIDGE_TOKEN must differ from EXECUTOR_API_TOKEN")
        if self.venue is VenueId.ALPACA_US:
            # Alpaca paper (environment TESTNET): keys come from the credentials loader only.
            if not self.symbol_list:
                raise ValueError("EXECUTOR_SYMBOLS required for ALPACA_US (e.g. SPY,QQQ,AAPL)")
            if self.bridge_url or self.bridge_token is not None:
                raise ValueError("EXECUTOR_BRIDGE_* belong to XP_B3, not ALPACA_US")
        return self

    @model_validator(mode="after")
    def watchdog_token_is_its_own(self) -> ExecutorSettings:
        token = self.watchdog_token
        if token is not None and token.get_secret_value() == self.api_token.get_secret_value():
            raise ValueError("EXECUTOR_WATCHDOG_TOKEN must differ from EXECUTOR_API_TOKEN")
        return self

    @property
    def symbol_list(self) -> list[str]:
        raw = self.symbols
        if raw is None:
            raw = _BINANCE_DEFAULT_SYMBOLS if self.venue is VenueId.BINANCE_SPOT else ""
        return [s.strip().upper() for s in raw.split(",") if s.strip()]

    @property
    def account_id(self) -> str:
        # binance-spot-testnet-main stays what it was: the persisted ledger is keyed by it.
        venue = self.venue.value.lower().replace("_", "-")
        return f"{venue}-{self.environment.value.lower()}-{self.account.lower()}"
