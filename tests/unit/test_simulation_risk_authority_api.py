from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def setup_function() -> None:
    client.post("/simulation/reset")


def _payload(*, quantity: float = 0.15) -> dict[str, object]:
    return {
        "order": {
            "market_id": "btc-usd-paper",
            "asset": "BTC",
            "side": "BUY",
            "quantity": quantity,
            "limit_price": 61_000.0,
        },
        "snapshot": {
            "symbol": "BTC",
            "market_id": "btc-usd-paper",
            "bid": 60_000.0,
            "ask": 60_010.0,
            "market_probability": 0.52,
        },
        "current_position_notional": 0.0,
        "limits": {
            "max_order_notional": 1_000_000.0,
            "max_position_notional": 1_000_000.0,
            "max_slippage_bps": 1_000.0,
        },
    }


def test_client_cannot_raise_server_order_notional_ceiling() -> None:
    response = client.post("/v1/simulate", json=_payload(quantity=0.2))
    body = response.json()

    assert response.status_code == 200
    assert body["accepted"] is False
    assert body["reason"] == "max order notional exceeded"
    assert client.get("/v1/fills").json()["count"] == 0


def test_api_uses_canonical_portfolio_exposure_when_client_underreports() -> None:
    first = client.post("/v1/simulate", json=_payload())
    second = client.post("/v1/simulate", json=_payload())
    third = client.post("/v1/simulate", json=_payload())

    assert first.json()["accepted"] is True
    assert second.json()["accepted"] is True
    assert third.json()["accepted"] is False
    assert third.json()["reason"] == "max position notional exceeded"
    assert client.get("/v1/fills").json()["count"] == 2
