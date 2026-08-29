from fastapi.testclient import TestClient
from proto_api.main import app

client = TestClient(app)


def test_edge_estimator_prefers_yes_when_fair_probability_is_higher() -> None:
    response = client.post(
        "/v1/edge",
        json={
            "market_id": "btc-above-threshold",
            "yes_bid": 0.44,
            "yes_ask": 0.46,
            "fair_probability": 0.52,
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["market_mid_probability"] == 0.45
    assert body["edge"] == 0.07
    assert body["edge_bps"] == 700
    assert body["side"] == "YES"


def test_edge_estimator_prefers_no_when_fair_probability_is_lower() -> None:
    response = client.post(
        "/v1/edge",
        json={
            "market_id": "eth-above-threshold",
            "yes_bid": 0.61,
            "yes_ask": 0.63,
            "fair_probability": 0.55,
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["edge"] == -0.07
    assert body["side"] == "NO"
