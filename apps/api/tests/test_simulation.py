from fastapi.testclient import TestClient
from proto_api.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "SIMULATION"


def test_simulated_buy_fill() -> None:
    payload = {
        "order": {
            "market_id": "btc-usd-paper",
            "asset": "BTC",
            "side": "BUY",
            "quantity": 0.01,
            "limit_price": 61000,
        },
        "snapshot": {
            "market_id": "btc-usd-paper",
            "asset": "BTC",
            "bid": 60000,
            "ask": 60010,
        },
    }
    response = client.post("/v1/simulate", json=payload)
    body = response.json()
    assert response.status_code == 200
    assert body["accepted"] is True
    assert body["fill"]["filled_quantity"] == 0.01
    assert body["fill"]["fill_price"] >= 60010


def test_risk_rejects_large_order() -> None:
    payload = {
        "order": {
            "market_id": "eth-usd-paper",
            "asset": "ETH",
            "side": "BUY",
            "quantity": 10,
            "limit_price": 4000,
        },
        "snapshot": {
            "market_id": "eth-usd-paper",
            "asset": "ETH",
            "bid": 3990,
            "ask": 4000,
        },
        "limits": {
            "max_order_notional": 10000,
            "max_position_notional": 25000,
            "max_slippage_bps": 75,
        },
    }
    response = client.post("/v1/simulate", json=payload)
    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert response.json()["reason"] == "max order notional exceeded"
