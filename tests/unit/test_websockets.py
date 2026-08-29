from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def test_websocket_channels_accept_and_respond_to_ping() -> None:
    for path, channel in [
        ("/ws/market-data", "market-data"),
        ("/ws/orderbook", "orderbook"),
        ("/ws/signals", "signals"),
        ("/ws/risk", "risk"),
        ("/ws/portfolio", "portfolio"),
        ("/ws/fills", "fills"),
        ("/ws/analytics", "analytics"),
    ]:
        with client.websocket_connect(path) as websocket:
            subscribed = websocket.receive_json()
            assert subscribed == {"type": "subscribed", "channel": channel}
            websocket.send_text("ping")
            assert websocket.receive_json() == {"type": "pong", "channel": channel}


def test_simulated_fill_is_broadcast_to_fill_channel() -> None:
    client.post("/simulation/reset")
    payload = {
        "order": {
            "market_id": "btc-usd-paper",
            "asset": "BTC",
            "side": "BUY",
            "quantity": 0.01,
            "limit_price": 61_000,
        },
        "snapshot": {
            "symbol": "BTC",
            "market_id": "btc-usd-paper",
            "bid": 60_000,
            "ask": 60_010,
            "market_probability": 0.52,
        },
    }

    with client.websocket_connect("/ws/fills") as websocket:
        assert websocket.receive_json()["type"] == "subscribed"
        response = client.post("/v1/simulate", json=payload)
        assert response.status_code == 200
        event = websocket.receive_json()
        assert event["type"] == "fill"
        assert event["data"]["market_id"] == "btc-usd-paper"
