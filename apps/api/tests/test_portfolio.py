from fastapi.testclient import TestClient
from proto_api.main import app

client = TestClient(app)


def setup_function() -> None:
    client.post("/v1/portfolio/reset")


def _simulate(side: str, quantity: float, limit_price: float, bid: float, ask: float) -> None:
    response = client.post(
        "/v1/simulate",
        json={
            "order": {
                "market_id": "btc-usd-paper",
                "asset": "BTC",
                "side": side,
                "quantity": quantity,
                "limit_price": limit_price,
            },
            "snapshot": {
                "market_id": "btc-usd-paper",
                "asset": "BTC",
                "bid": bid,
                "ask": ask,
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["accepted"] is True


def test_portfolio_tracks_simulated_position() -> None:
    _simulate("BUY", 0.01, 61000, 60000, 60010)

    response = client.get("/v1/portfolio")
    body = response.json()

    assert response.status_code == 200
    assert body["mode"] == "SIMULATION"
    assert len(body["positions"]) == 1
    assert body["positions"][0]["asset"] == "BTC"
    assert body["positions"][0]["quantity"] == 0.01
    assert body["positions"][0]["fees"] > 0


def test_portfolio_realizes_pnl_on_close() -> None:
    _simulate("BUY", 0.01, 61000, 60000, 60010)
    _simulate("SELL", 0.01, 60890, 60890, 60900)

    body = client.get("/v1/portfolio").json()
    position = body["positions"][0]

    assert position["quantity"] == 0
    assert position["average_price"] == 0
    assert position["realized_pnl"] > 0
    assert body["total_fees"] > 0
