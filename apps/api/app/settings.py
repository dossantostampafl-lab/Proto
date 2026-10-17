from __future__ import annotations

import re

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .safety_policy import SafetyPolicyError, validate_runtime_mode

_SYMBOL_RE = re.compile(r"^[A-Z0-9.-]{1,40}$")
_ALLOWED_ALPACA_EQUITY_FEEDS = frozenset(
    {"iex", "sip", "delayed_sip", "otc", "boats", "overnight"}
)
_MIN_PRODUCTION_SECRET_LENGTH = 32


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
    alpaca_equity_symbols: str = ""
    alpaca_market_data_key_id: str | None = None
    alpaca_market_data_secret_key: str | None = None
    alpaca_equity_feed: str = "iex"
    brapi_equity_symbols: str = ""
    brapi_market_data_token: str | None = None
    equity_market_data_max_age_seconds: float | None = Field(default=None, gt=0, le=3_600)
    creation_bridge_shared_secret: str | None = None
    # TradingView webhook (Phase 1). Unset secret keeps the endpoint closed (503).
    tradingview_webhook_secret: str | None = None
    tradingview_instruments: str = (
        "BINANCE_SPOT:BTCBRL,BINANCE_SPOT:ETHBRL,BINANCE_SPOT:SOLBRL"
    )
    tradingview_ip_allowlist_enabled: bool = True
    # Trust the right-most X-Forwarded-For entry; only behind our own reverse proxy.
    tradingview_trust_forwarded_for: bool = False
    tradingview_max_alert_age_seconds: float = Field(
        default=30.0, gt=0.0, le=300.0, allow_inf_nan=False
    )
    # Hybrid agent pipeline (Phase 2). Proposals only: no order authority.
    agent_pipeline_enabled: bool = False
    # PROTO's own strategy (EMA cross on live mids) feeds the pipeline next to TradingView.
    proto_strategy_enabled: bool = False
    # Trader-agent roster: route each signal to the ACTIVE agent of its slot, sized on that
    # agent's virtual R$200 (docs/agents/roster.md). Tightens sizing only; off keeps the
    # account-level pipeline exactly as it was.
    agent_roster_enabled: bool = False
    # Run the closed-loop drill (signal -> permit -> executor testnet -> reconciliation) once
    # after start, when the executor backend is configured.
    e2e_drill_on_start: bool = False
    # "deterministic" approves every sized candidate; "claude" adds the AI reviewer,
    # which may only reduce, defer or veto. Switch only after the offline gate passes.
    agent_reviewer: str = "deterministic"
    agent_model: str = "claude-opus-5-5"
    agent_effort: str = "low"
    agent_timeout_seconds: float = Field(default=15.0, gt=0.0, le=60.0, allow_inf_nan=False)
    agent_stop_distance: float = Field(default=0.02, gt=0.0, lt=0.5, allow_inf_nan=False)
    # Risk Engine (Phase 3). Unset key -> ephemeral per-process key (permits live seconds).
    risk_permit_signing_key: str | None = None
    # Ed25519 seed (base64, 32 bytes). Takes precedence over the HMAC key; required for canary.
    risk_permit_private_key: str | None = None
    risk_permit_ttl_seconds: float = Field(default=10.0, gt=0.0, le=60.0, allow_inf_nan=False)
    risk_stop_distance: float = Field(default=0.02, gt=0.0, lt=0.5, allow_inf_nan=False)
    risk_price_collar_bps: float = Field(default=50.0, gt=0.0, le=500.0, allow_inf_nan=False)
    risk_max_quote_age_seconds: float = Field(default=10.0, gt=0.0, le=60.0, allow_inf_nan=False)
    # OMS (Phase 4): send approved proposals to the paper venue. Paper only.
    oms_paper_execution_enabled: bool = False
    oms_paper_fee_bps: float = Field(default=10.0, ge=0.0, le=100.0, allow_inf_nan=False)
    oms_paper_slippage_bps: float = Field(default=2.0, ge=0.0, le=100.0, allow_inf_nan=False)
    # Phase 5b: "paper" runs the OMS in-process against the paper venue; "executor" sends
    # permitted intents to the isolated executor (Binance testnet) over the private network.
    oms_execution_backend: str = "paper"
    executor_url: str | None = None
    executor_api_token: str | None = None
    # XP (B3) through MT5: a second executor, one per venue. Unset leaves XP routing off and the
    # API byte-for-byte as it was.
    xp_executor_url: str | None = None
    xp_executor_api_token: str | None = None
    operator_api_token: str | None = None
    # Phase 6b: the independent watchdog's token. It can only halt and report a heartbeat.
    watchdog_api_token: str | None = None
    # Public HTTPS origin of the deployed dashboard (e.g. the Oracle Cloud host).
    public_origin: str | None = None
    http_rate_limit_per_minute: int = Field(default=600, ge=1, le=100_000)
    minimum_net_edge: float = Field(default=0.01, ge=0.0, le=1.0, allow_inf_nan=False)
    minimum_confidence: float = Field(default=0.55, ge=0.0, le=1.0, allow_inf_nan=False)
    # Server cap on absolute paper position size per asset, in units.
    max_position: float = Field(default=10.0, gt=0.0, allow_inf_nan=False)
    simulation_max_open_positions: int = Field(default=10, ge=1, le=1_000)
    # When true, risk-increasing paper orders must clear minimum_net_edge and
    # minimum_confidence, computed server-side with the simulator's own costs.
    simulation_edge_gate_enabled: bool = False
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

    @field_validator("agent_reviewer")
    @classmethod
    def enforce_agent_reviewer(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"deterministic", "claude"}:
            raise ValueError("agent_reviewer must be deterministic or claude")
        return normalized

    @field_validator("agent_effort")
    @classmethod
    def enforce_agent_effort(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("agent_effort must be low, medium, high, xhigh or max")
        return normalized

    @field_validator("oms_execution_backend")
    @classmethod
    def enforce_oms_execution_backend(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"paper", "executor"}:
            raise ValueError("oms_execution_backend must be paper or executor")
        return normalized

    @model_validator(mode="after")
    def enforce_executor_backend(self) -> Settings:
        if self.oms_execution_backend != "executor":
            return self
        if not self.executor_url:
            raise ValueError("EXECUTOR_URL is required with OMS_EXECUTION_BACKEND=executor")
        token = self.executor_api_token or ""
        if len(token) < _MIN_PRODUCTION_SECRET_LENGTH:
            raise ValueError("EXECUTOR_API_TOKEN must be at least 32 characters")
        # The executor must be able to verify permits; an ephemeral key never matches.
        if not (self.risk_permit_signing_key or self.risk_permit_private_key):
            raise ValueError(
                "RISK_PERMIT_SIGNING_KEY or RISK_PERMIT_PRIVATE_KEY is required with the executor"
            )
        return self

    @model_validator(mode="after")
    def enforce_xp_executor(self) -> Settings:
        if not (self.xp_executor_url or self.xp_executor_api_token):
            return self
        if self.oms_execution_backend != "executor":
            raise ValueError("XP_EXECUTOR_URL needs OMS_EXECUTION_BACKEND=executor")
        if not self.xp_executor_url:
            raise ValueError("XP_EXECUTOR_URL is required with XP_EXECUTOR_API_TOKEN")
        token = self.xp_executor_api_token or ""
        if len(token) < _MIN_PRODUCTION_SECRET_LENGTH:
            raise ValueError("XP_EXECUTOR_API_TOKEN must be at least 32 characters")
        if token in {self.executor_api_token, self.operator_api_token, self.watchdog_api_token}:
            raise ValueError("XP_EXECUTOR_API_TOKEN must differ from the other API tokens")
        return self

    @property
    def xp_routing_enabled(self) -> bool:
        return bool(
            self.oms_execution_backend == "executor"
            and self.xp_executor_url
            and self.xp_executor_api_token
        )

    @field_validator("live_market_source")
    @classmethod
    def enforce_live_market_source(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"COINBASE", "BINANCE"}:
            raise ValueError("live_market_source must be COINBASE or BINANCE")
        return normalized

    @field_validator("alpaca_equity_feed")
    @classmethod
    def enforce_alpaca_equity_feed(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_ALPACA_EQUITY_FEEDS:
            raise ValueError("unsupported Alpaca equity feed")
        return normalized

    @field_validator(
        "alpaca_market_data_key_id",
        "alpaca_market_data_secret_key",
        "brapi_market_data_token",
        "creation_bridge_shared_secret",
        "operator_api_token",
        "tradingview_webhook_secret",
        "risk_permit_signing_key",
        "executor_api_token",
        "xp_executor_api_token",
        "risk_permit_private_key",
        "watchdog_api_token",
    )
    @classmethod
    def normalize_optional_secret(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def enforce_production_authentication_strength(self) -> Settings:
        if self.app_env.strip().lower() != "production":
            return self
        if (
            self.operator_api_token is None
            or len(self.operator_api_token) < _MIN_PRODUCTION_SECRET_LENGTH
        ):
            raise ValueError("OPERATOR_API_TOKEN must be at least 32 characters in production")
        if (
            self.creation_bridge_shared_secret is not None
            and len(self.creation_bridge_shared_secret) < _MIN_PRODUCTION_SECRET_LENGTH
        ):
            raise ValueError(
                "CREATION_BRIDGE_SHARED_SECRET must be at least 32 characters in production"
            )
        if (
            self.tradingview_webhook_secret is not None
            and len(self.tradingview_webhook_secret) < _MIN_PRODUCTION_SECRET_LENGTH
        ):
            raise ValueError(
                "TRADINGVIEW_WEBHOOK_SECRET must be at least 32 characters in production"
            )
        if (
            self.risk_permit_signing_key is not None
            and len(self.risk_permit_signing_key) < _MIN_PRODUCTION_SECRET_LENGTH
        ):
            raise ValueError(
                "RISK_PERMIT_SIGNING_KEY must be at least 32 characters in production"
            )
        if (
            self.watchdog_api_token is not None
            and len(self.watchdog_api_token) < _MIN_PRODUCTION_SECRET_LENGTH
        ):
            raise ValueError("WATCHDOG_API_TOKEN must be at least 32 characters in production")
        if not self.tradingview_ip_allowlist_enabled:
            raise ValueError("TRADINGVIEW_IP_ALLOWLIST_ENABLED must stay true in production")
        return self

    @model_validator(mode="after")
    def watchdog_token_is_its_own(self) -> Settings:
        if self.watchdog_api_token is not None and self.watchdog_api_token in {
            self.operator_api_token,
            self.executor_api_token,
        }:
            raise ValueError("WATCHDOG_API_TOKEN must differ from the operator and executor tokens")
        return self

    @model_validator(mode="after")
    def enforce_production_persistence_boundary(self) -> Settings:
        production = self.app_env.strip().lower() == "production"
        live_monitoring = self.system_mode == "LIVE_MONITORING"
        if production and live_monitoring and self.persistence_enabled:
            raise ValueError(
                "PERSISTENCE_ENABLED must be false in production LIVE_MONITORING; "
                "use ORCHESTRATION_PERSISTENCE_ENABLED for durable orchestration state"
            )
        return self

    @staticmethod
    def _parse_symbol_csv(value: str) -> tuple[str, ...]:
        symbols = {item.strip().upper() for item in value.split(",") if item.strip()}
        invalid = sorted(symbol for symbol in symbols if _SYMBOL_RE.fullmatch(symbol) is None)
        if invalid:
            raise ValueError("equity symbol allowlists contain invalid symbol syntax")
        return tuple(sorted(symbols))

    @property
    def tradingview_instrument_allowlist(self) -> frozenset[str]:
        return frozenset(
            item.strip().upper() for item in self.tradingview_instruments.split(",") if item.strip()
        )

    @property
    def alpaca_equity_allowlist(self) -> tuple[str, ...]:
        return self._parse_symbol_csv(self.alpaca_equity_symbols)

    @property
    def brapi_equity_allowlist(self) -> tuple[str, ...]:
        return self._parse_symbol_csv(self.brapi_equity_symbols)

    @property
    def alpaca_market_data_configured(self) -> bool:
        return (
            self.alpaca_market_data_key_id is not None
            and self.alpaca_market_data_secret_key is not None
        )

    @property
    def creation_bridge_configured(self) -> bool:
        return self.creation_bridge_shared_secret is not None

    @property
    def database_driver(self) -> str:
        """Return only the non-sensitive SQLAlchemy driver identifier."""
        return self.database_url.strip().split(":", maxsplit=1)[0].lower()

    @property
    def durable_database_configured(self) -> bool:
        return self.database_driver in {
            "postgres",
            "postgresql",
            "postgresql+asyncpg",
        }

    @property
    def orchestration_persistence_active(self) -> bool:
        """Resolve durable orchestration storage without enabling simulation persistence.

        The dedicated switch remains the primary opt-in. In production, an explicitly
        configured durable PostgreSQL DATABASE_URL is itself sufficient evidence that
        orchestration/decision-memory may reuse that database even when deployment-level
        environment variables override image defaults. This never enables simulation
        persistence and never relaxes the real-money execution boundary.
        """
        if self.orchestration_persistence_enabled:
            return True
        production = self.app_env.strip().lower() == "production"
        if production and self.durable_database_configured:
            return True
        return self.live_persistence_enabled and self.durable_database_configured

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
