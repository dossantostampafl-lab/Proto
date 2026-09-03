from fastapi.testclient import TestClient

from apps.api.app.railway_app import app
from apps.api.app.settings import settings


def test_configured_us_equity_fails_closed_when_provider_credentials_are_absent(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "alpaca_equity_symbols", "AAPL")

    with TestClient(app) as client:
        response = client.get("/equity-market/US:AAPL")

    assert response.status_code == 503
    payload = response.json()
    assert payload["detail"] == "US equity market-data provider is not configured"
