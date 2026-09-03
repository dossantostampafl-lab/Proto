from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .universal import MarketEvent, MarketEventKind, MarketEventProvenance

_ALPACA_BASE_URL = "https://data.alpaca.markets"
_BRAPI_BASE_URL = "https://brapi.dev"
_BRAPI_UNAUTHENTICATED_SANDBOX = frozenset({"PETR4", "MGLU3", "VALE3", "ITUB4"})
_ALLOWED_ALPACA_FEEDS = frozenset(
    {"iex", "sip", "delayed_sip", "otc", "boats", "overnight"}
)


class ReadOnlyProviderError(RuntimeError):
    pass


class AlpacaReadOnlyConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    api_key_id: str = Field(min_length=1)
    api_secret_key: str = Field(min_length=1)
    feed: str = "iex"
    allowed_symbols: frozenset[str] = Field(min_length=1)
    timeout_seconds: float = Field(default=5.0, gt=0, le=30)

    @field_validator("feed")
    @classmethod
    def validate_feed(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_ALPACA_FEEDS:
            raise ValueError("unsupported Alpaca equity feed")
        return normalized

    @field_validator("allowed_symbols")
    @classmethod
    def normalize_symbols(cls, value: frozenset[str]) -> frozenset[str]:
        normalized = frozenset(symbol.strip().upper() for symbol in value if symbol.strip())
        if not normalized:
            raise ValueError("allowed_symbols must not be empty")
        return normalized


class BrapiReadOnlyConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    token: str | None = Field(default=None, min_length=1)
    allowed_symbols: frozenset[str] = Field(min_length=1)
    timeout_seconds: float = Field(default=5.0, gt=0, le=30)

    @field_validator("allowed_symbols")
    @classmethod
    def normalize_symbols(cls, value: frozenset[str]) -> frozenset[str]:
        normalized = frozenset(symbol.strip().upper() for symbol in value if symbol.strip())
        if not normalized:
            raise ValueError("allowed_symbols must not be empty")
        return normalized

    @field_validator("token")
    @classmethod
    def normalize_token(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class AlpacaEquityReadOnlyProvider:
    """Authenticated market-data client with no trading/order methods."""

    def __init__(
        self,
        config: AlpacaReadOnlyConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._client = client

    async def latest_quote(self, symbol: str) -> MarketEvent:
        normalized = self._require_allowed_symbol(symbol)
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.config.timeout_seconds)
        try:
            response = await client.get(
                f"{_ALPACA_BASE_URL}/v2/stocks/{normalized}/quotes/latest",
                params={"feed": self.config.feed, "currency": "USD"},
                headers={
                    "APCA-API-KEY-ID": self.config.api_key_id,
                    "APCA-API-SECRET-KEY": self.config.api_secret_key,
                },
            )
            self._raise_for_status(response, provider="ALPACA")
            payload = response.json()
            quote = payload.get("quote")
            if not isinstance(quote, dict):
                raise ReadOnlyProviderError("ALPACA response missing quote object")
            return self._parse_quote(normalized, quote)
        finally:
            if own_client:
                await client.aclose()

    def _require_allowed_symbol(self, symbol: str) -> str:
        normalized = symbol.strip().upper()
        if normalized not in self.config.allowed_symbols:
            raise ValueError("symbol is outside the configured Alpaca allowlist")
        return normalized

    @staticmethod
    def _raise_for_status(response: httpx.Response, *, provider: str) -> None:
        if response.status_code == 429:
            raise ReadOnlyProviderError(f"{provider} rate limit reached")
        if response.status_code in {401, 403}:
            raise ReadOnlyProviderError(f"{provider} credentials/plan rejected")
        if response.is_error:
            raise ReadOnlyProviderError(f"{provider} market-data request failed")

    def _parse_quote(self, symbol: str, quote: dict[str, Any]) -> MarketEvent:
        try:
            observed_at = _parse_utc_timestamp(quote["t"])
            bid = float(quote["bp"])
            ask = float(quote["ap"])
            bid_size = float(quote.get("bs", 0.0))
            ask_size = float(quote.get("as", 0.0))
        except (KeyError, TypeError, ValueError) as exc:
            raise ReadOnlyProviderError("ALPACA quote payload is invalid") from exc
        try:
            return MarketEvent(
                instrument_id=f"US:{symbol}",
                kind=MarketEventKind.QUOTE,
                observed_at=observed_at,
                received_at=datetime.now(UTC),
                source=f"ALPACA_{self.config.feed.upper()}",
                provenance=MarketEventProvenance.LICENSED_READ_ONLY,
                bid=bid,
                ask=ask,
                bid_size=bid_size,
                ask_size=ask_size,
            )
        except ValidationError as exc:
            raise ReadOnlyProviderError(
                "ALPACA quote is invalid or non-executable"
            ) from exc


class BrapiEquityReadOnlyProvider:
    """B3 quote client using brapi.dev's documented read-only quote endpoint."""

    def __init__(
        self,
        config: BrapiReadOnlyConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._client = client
        if config.token is None and not config.allowed_symbols.issubset(
            _BRAPI_UNAUTHENTICATED_SANDBOX
        ):
            raise ValueError(
                "unauthenticated brapi provider is restricted to documented sandbox symbols"
            )

    async def latest_price(self, symbol: str) -> MarketEvent:
        normalized = self._require_allowed_symbol(symbol)
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.config.timeout_seconds)
        headers = (
            {"Authorization": f"Bearer {self.config.token}"}
            if self.config.token is not None
            else {}
        )
        try:
            response = await client.get(
                f"{_BRAPI_BASE_URL}/api/v2/stocks/quote",
                params={"symbols": normalized},
                headers=headers,
            )
            AlpacaEquityReadOnlyProvider._raise_for_status(response, provider="BRAPI")
            payload = response.json()
            return self._parse_price(normalized, payload)
        finally:
            if own_client:
                await client.aclose()

    def _require_allowed_symbol(self, symbol: str) -> str:
        normalized = symbol.strip().upper()
        if normalized not in self.config.allowed_symbols:
            raise ValueError("symbol is outside the configured brapi allowlist")
        return normalized

    @staticmethod
    def _parse_price(symbol: str, payload: Any) -> MarketEvent:
        if not isinstance(payload, dict):
            raise ReadOnlyProviderError("BRAPI response must be an object")
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            raise ReadOnlyProviderError("BRAPI response missing results")
        result = next(
            (
                item
                for item in results
                if isinstance(item, dict)
                and str(item.get("symbol", item.get("requestedSymbol", ""))).upper() == symbol
            ),
            None,
        )
        if result is None:
            raise ReadOnlyProviderError("BRAPI response missing requested symbol")
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        try:
            last = float(data["regularMarketPrice"])
            volume_raw = data.get("regularMarketVolume")
            volume = float(volume_raw) if volume_raw is not None else None
            requested_at = _parse_utc_timestamp(payload["requestedAt"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ReadOnlyProviderError("BRAPI quote payload is invalid") from exc
        try:
            return MarketEvent(
                instrument_id=f"B3:{symbol}",
                kind=MarketEventKind.STATUS,
                observed_at=requested_at,
                received_at=datetime.now(UTC),
                source="BRAPI_V2",
                provenance=MarketEventProvenance.PUBLIC_READ_ONLY,
                last=last,
                volume=volume,
                quality_flags=("REQUEST_TIME_NOT_EXCHANGE_TIMESTAMP",),
            )
        except ValidationError as exc:
            raise ReadOnlyProviderError("BRAPI quote is invalid") from exc


def _parse_utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)
