from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def test_operational_synthetic_surfaces_are_disabled_by_default() -> None:
    for path in (
        "/markets/btc-threshold",
        "/market-data/BTC",
        "/orderbook/BTC",
        "/data-quality/BTC",
        "/probability/btc-threshold",
        "/edge/btc-threshold",
        "/expected-value/btc-threshold",
        "/analytics/greeks/btc-threshold",
        "/hawkes/BTC",
        "/market-lifecycle",
        "/resolution-grid",
        "/analytics/expiry-map",
    ):
        response = client.get(path)
        assert response.status_code == 503, path
        assert response.json()["detail"] == "synthetic research surface disabled"


def test_canonical_simulation_portfolio_surfaces_remain_available() -> None:
    for path in ("/portfolio", "/positions", "/pnl"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.json()["real_money_execution"] is False
