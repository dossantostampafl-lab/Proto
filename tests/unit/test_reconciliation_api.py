from fastapi.testclient import TestClient

from apps.api.app.main import app, portfolio

client = TestClient(app)


def _simulate_buy() -> None:
    client.post("/simulation/reset")
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
                "symbol": "BTC",
                "market_id": "btc-usd-paper",
                "bid": 60_000,
                "ask": 60_010,
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["accepted"] is True


def test_reconciliation_endpoint_confirms_simulated_state() -> None:
    _simulate_buy()

    body = client.get("/v1/reconciliation").json()

    assert body["consistent"] is True
    assert body["issues"] == []
    assert body["journal_fill_count"] == 1
    assert body["authoritative_fill_count"] == 1


def test_reconciliation_endpoint_detects_position_divergence(monkeypatch) -> None:
    _simulate_buy()
    original_snapshot = portfolio.snapshot

    def divergent_snapshot(*args, **kwargs):
        snapshot = original_snapshot(*args, **kwargs)
        snapshot["positions"][0]["quantity"] = 99.0
        return snapshot

    monkeypatch.setattr(portfolio, "snapshot", divergent_snapshot)

    body = client.get("/v1/reconciliation").json()

    assert body["consistent"] is False
    assert body["issues"] == ["POSITION_MISMATCH"]

