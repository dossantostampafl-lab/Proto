import json

import httpx
import pytest

from services.market_data.equity_readonly import (
    AlpacaEquityReadOnlyProvider,
    AlpacaReadOnlyConfig,
    BrapiEquityReadOnlyProvider,
    BrapiReadOnlyConfig,
    ReadOnlyProviderError,
)
from services.market_data.universal import MarketEventKind, MarketEventProvenance


@pytest.mark.asyncio
async def test_alpaca_latest_quote_preserves_read_only_provenance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["APCA-API-KEY-ID"] == "test-key"
        assert request.headers["APCA-API-SECRET-KEY"] == "test-secret"
        assert request.url.params["feed"] == "iex"
        return httpx.Response(
            200,
            json={
                "symbol": "AAPL",
                "quote": {
                    "t": "2026-09-02T19:59:59.950348Z",
                    "bp": 100.0,
                    "ap": 100.1,
                    "bs": 10,
                    "as": 12,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AlpacaEquityReadOnlyProvider(
            AlpacaReadOnlyConfig(
                api_key_id="test-key",
                api_secret_key="test-secret",
                feed="iex",
                allowed_symbols=frozenset({"AAPL"}),
            ),
            client=client,
        )
        event = await provider.latest_quote("aapl")

    assert event.instrument_id == "US:AAPL"
    assert event.kind is MarketEventKind.QUOTE
    assert event.provenance is MarketEventProvenance.LICENSED_READ_ONLY
    assert event.bid == 100.0
    assert event.ask == 100.1


@pytest.mark.asyncio
async def test_alpaca_symbol_allowlist_fails_closed() -> None:
    provider = AlpacaEquityReadOnlyProvider(
        AlpacaReadOnlyConfig(
            api_key_id="test-key",
            api_secret_key="test-secret",
            allowed_symbols=frozenset({"AAPL"}),
        )
    )
    with pytest.raises(ValueError, match="allowlist"):
        await provider.latest_quote("NVDA")


def test_unauthenticated_brapi_is_restricted_to_documented_sandbox() -> None:
    with pytest.raises(ValueError, match="sandbox symbols"):
        BrapiEquityReadOnlyProvider(
            BrapiReadOnlyConfig(
                allowed_symbols=frozenset({"PETR4", "WEGE3"}),
            )
        )


@pytest.mark.asyncio
async def test_brapi_price_marks_request_timestamp_limitation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbols"] == "PETR4"
        assert "Authorization" not in request.headers
        payload = {
            "results": [
                {
                    "requestedSymbol": "PETR4",
                    "symbol": "PETR4",
                    "data": {
                        "shortName": "Test",
                        "currency": "BRL",
                        "regularMarketPrice": 38.5,
                        "regularMarketVolume": 1000,
                    },
                }
            ],
            "requestedAt": "2026-09-02T17:08:02.000Z",
            "took": 10,
        }
        return httpx.Response(200, content=json.dumps(payload).encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BrapiEquityReadOnlyProvider(
            BrapiReadOnlyConfig(allowed_symbols=frozenset({"PETR4"})),
            client=client,
        )
        event = await provider.latest_price("PETR4")

    assert event.instrument_id == "B3:PETR4"
    assert event.kind is MarketEventKind.STATUS
    assert event.last == 38.5
    assert event.provenance is MarketEventProvenance.PUBLIC_READ_ONLY
    assert "REQUEST_TIME_NOT_EXCHANGE_TIMESTAMP" in event.quality_flags


@pytest.mark.asyncio
async def test_provider_auth_and_rate_limit_errors_do_not_leak_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AlpacaEquityReadOnlyProvider(
            AlpacaReadOnlyConfig(
                api_key_id="secret-id",
                api_secret_key="secret-value",
                allowed_symbols=frozenset({"AAPL"}),
            ),
            client=client,
        )
        with pytest.raises(ReadOnlyProviderError) as error:
            await provider.latest_quote("AAPL")
    message = str(error.value)
    assert "secret-id" not in message
    assert "secret-value" not in message
