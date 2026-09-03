from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import apps.api.app.equity_market_surface as equity_surface
from apps.api.app.railway_app import app
from apps.api.app.settings import settings
from services.market_data import MarketEvent, MarketEventKind, MarketEventProvenance
from services.market_data.equity_readonly import (
    AlpacaEquityReadOnlyProvider,
    AlpacaReadOnlyConfig,
    ReadOnlyProviderError,
)


def test_configured_us_equity_fails_closed_when_provider_credentials_are_absent(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "alpaca_equity_symbols", "AAPL")
    monkeypatch.setattr(settings, "alpaca_market_data_key_id", None)
    monkeypatch.setattr(settings, "alpaca_market_data_secret_key", None)

    with TestClient(app) as client:
        response = client.get("/equity-market/US:AAPL")

    assert response.status_code == 503
    payload = response.json()
    assert payload["detail"] == "US equity market-data provider is not configured"


def test_us_equity_observation_exposes_truthful_provenance_without_inventing_freshness(
    monkeypatch,
) -> None:
    observed = datetime.now(UTC)

    class FakeProvider:
        def __init__(self, _config) -> None:
            pass

        async def latest_quote(self, symbol: str) -> MarketEvent:
            assert symbol == "AAPL"
            return MarketEvent(
                instrument_id="US:AAPL",
                kind=MarketEventKind.QUOTE,
                observed_at=observed,
                received_at=observed,
                source="ALPACA_IEX",
                provenance=MarketEventProvenance.LICENSED_READ_ONLY,
                bid=100.0,
                ask=100.1,
                bid_size=5.0,
                ask_size=6.0,
            )

    monkeypatch.setattr(settings, "alpaca_equity_symbols", "AAPL")
    monkeypatch.setattr(settings, "alpaca_market_data_key_id", "read-only-key")
    monkeypatch.setattr(settings, "alpaca_market_data_secret_key", "read-only-secret")
    monkeypatch.setattr(settings, "equity_market_data_max_age_seconds", None)
    monkeypatch.setattr(equity_surface, "AlpacaEquityReadOnlyProvider", FakeProvider)

    with TestClient(app) as client:
        response = client.get("/equity-market/US:AAPL")

    assert response.status_code == 200
    payload = response.json()
    assert payload["event"]["provenance"] == "LICENSED_READ_ONLY"
    assert payload["read_only_market_data"] is True
    assert payload["currently_fresh"] is None
    assert payload["freshness_threshold_seconds"] is None
    assert payload["execution_connected"] is False
    assert payload["financial_connectivity"] is False
    assert payload["real_money_execution"] is False


def test_b3_observation_uses_public_read_only_provenance_and_explicit_staleness(
    monkeypatch,
) -> None:
    observed = datetime.now(UTC) - timedelta(seconds=5)

    class FakeBrapiProvider:
        def __init__(self, _config) -> None:
            pass

        async def latest_price(self, symbol: str) -> MarketEvent:
            assert symbol == "PETR4"
            return MarketEvent(
                instrument_id="B3:PETR4",
                kind=MarketEventKind.STATUS,
                observed_at=observed,
                received_at=observed,
                source="BRAPI_V2",
                provenance=MarketEventProvenance.PUBLIC_READ_ONLY,
                last=32.5,
                volume=1000.0,
                quality_flags=("REQUEST_TIME_NOT_EXCHANGE_TIMESTAMP",),
            )

    monkeypatch.setattr(settings, "brapi_equity_symbols", "PETR4")
    monkeypatch.setattr(settings, "equity_market_data_max_age_seconds", 1.0)
    monkeypatch.setattr(equity_surface, "BrapiEquityReadOnlyProvider", FakeBrapiProvider)

    with TestClient(app) as client:
        response = client.get("/equity-market/B3:PETR4")

    assert response.status_code == 200
    payload = response.json()
    assert payload["event"]["provenance"] == "PUBLIC_READ_ONLY"
    assert payload["currently_fresh"] is False
    assert payload["freshness_threshold_seconds"] == 1.0
    assert payload["execution_connected"] is False
    assert payload["financial_connectivity"] is False
    assert payload["real_money_execution"] is False


def test_provider_failure_returns_502_without_fabricating_market_event(monkeypatch) -> None:
    class FailingProvider:
        def __init__(self, _config) -> None:
            pass

        async def latest_quote(self, _symbol: str) -> MarketEvent:
            raise ReadOnlyProviderError("ALPACA market-data request failed")

    monkeypatch.setattr(settings, "alpaca_equity_symbols", "AAPL")
    monkeypatch.setattr(settings, "alpaca_market_data_key_id", "read-only-key")
    monkeypatch.setattr(settings, "alpaca_market_data_secret_key", "read-only-secret")
    monkeypatch.setattr(equity_surface, "AlpacaEquityReadOnlyProvider", FailingProvider)

    with TestClient(app) as client:
        response = client.get("/equity-market/US:AAPL")

    assert response.status_code == 502
    assert response.json() == {"detail": "ALPACA market-data request failed"}


def test_alpaca_zero_bid_quote_is_reported_as_provider_error() -> None:
    provider = AlpacaEquityReadOnlyProvider(
        AlpacaReadOnlyConfig(
            api_key_id="read-only-key",
            api_secret_key="read-only-secret",
            allowed_symbols=frozenset({"AAPL"}),
        )
    )
    quote = {
        "t": "2026-09-03T20:00:00Z",
        "bp": 0,
        "ap": 100.0,
        "bs": 1,
        "as": 1,
    }

    with pytest.raises(ReadOnlyProviderError, match="invalid or non-executable"):
        provider._parse_quote("AAPL", quote)
