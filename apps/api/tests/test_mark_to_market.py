from fastapi.testclient import TestClient

from proto_api.main import app

client = TestClient(app)


def setup_function() -> None:
    client.post("/v1/portfolio/reset")


def _buy_btc() -> None:
    response = client.post(
        "/v1/simulate",
        json={
            "order": {
                "market_id": "btc-usd-paper",
                "asset": "BTC",
                "side": "BUY",
                "quantity": 0.01,
                "limit_price": 61_000,
            },
            "snapshot": {
                "market_id": "btc-usd-paper",
                "asset": "BTC",
                "bid": 60_000,
                "ask": 60_010,
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["accepted"] is True


def test_mark_to_market_calculates_unrealized_pnl() -> None:
    _buy_btc()

    response = client.post(
        "/v1/portfolio/mark",
        json={"marks": [{"asset": "BTC", "price": 61_000}]},
    )
    body = response.json()
    position = body["positions"][0]

    assert response.status_code == 200
    assert position["mark_price"] == 61_000
    assert position["market_value"] == 610
    assert position["unrealized_pnl"] > 0
    assert body["total_unrealized_pnl"] > 0
    assert body["total_pnl_after_fees"] < body["total_unrealized_pnl"]


def test_fill_journal_records_only_simulated_fills() -> None:
    _buy_btc()

    response = client.get("/v1/fills?limit=10")
    body = response.json()

    assert response.status_code == 200
    assert body["mode"] == "SIMULATION"
    assert body["count"] == 1
    assert body["fills"][0]["asset"] == "BTC"
    assert body["fills"][0]["side"] == "BUY"
    assert body["fills"][0]["filled_quantity"] == 0.01


def test_reset_clears_positions_and_journal() -> None:
    _buy_btc()
    client.post("/v1/portfolio/reset")

    portfolio = client.get("/v1/portfolio").json()
    fills = client.get("/v1/fills").json()

    assert portfolio["positions"] == []
    assert fills["count"] == 0
