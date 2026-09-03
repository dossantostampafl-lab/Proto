from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import apps.api.app.equity_market_surface as equity_surface
from apps.api.app.railway_app import app
from apps.api.app.settings import settings
from services.market_data import MarketEvent, MarketEventKind, MarketEventProvenance
from services.market_data.equity_readonly import AlpacaEquityReadOnlyProvider, ReadOnlyProviderError


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


def test_alpaca_zero_bid_quote_is_reported_as_provider_error() -> None:
    provider = object.__new__(AlpacaEquityReadOnlyProvider)
    quote = {
        "t": "2026-09-03T20:00:00Z",
        "bp": 0,
        "ap": 100.0,
        "bs": 1,
        "as": 1,
    }

    with pytest.raises(ReadOnlyProviderError, match="invalid or non-executable"):
        provider._parse_quote("AAPL", quote)
