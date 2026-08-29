from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def setup_function() -> None:
    client.post("/simulation/reset")


def _buy_payload() -> dict[str, object]:
    return {
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


def test_health_exposes_simulation_only_runtime() -> None:
    response = client.get("/health")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["mode"] == "SIMULATION"
    assert body["persistence_enabled"] is False


def test_simulated_fill_updates_portfolio_and_journal() -> None:
    response = client.post("/v1/simulate", json=_buy_payload())
    body = response.json()

    assert response.status_code == 200
    assert body["accepted"] is True
    assert body["fill"]["filled_quantity"] == 0.01

    portfolio = client.get("/v1/portfolio").json()
    fills = client.get("/v1/fills").json()

    assert portfolio["positions"][0]["asset"] == "BTC"
    assert portfolio["positions"][0]["quantity"] == 0.01
    assert fills["count"] == 1
    assert fills["fills"][0]["market_id"] == "btc-usd-paper"


def test_mark_to_market_calculates_unrealized_pnl() -> None:
    client.post("/v1/simulate", json=_buy_payload())

    response = client.post(
        "/v1/portfolio/mark",
        json={"marks": [{"asset": "BTC", "price": 61_000}]},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["positions"][0]["mark_price"] == 61_000
    assert body["positions"][0]["unrealized_pnl"] > 0
    assert body["total_pnl_after_fees"] < body["total_unrealized_pnl"]


def test_kill_switch_halts_only_simulated_execution() -> None:
    trigger = client.post("/killswitch/trigger")
    assert trigger.status_code == 200
    assert trigger.json()["running"] is False

    response = client.post("/v1/simulate", json=_buy_payload())
    body = response.json()

    assert response.status_code == 200
    assert body["accepted"] is False
    assert body["reason"] == "simulation halted"
    assert client.get("/risk").json()["real_money_execution"] is False
