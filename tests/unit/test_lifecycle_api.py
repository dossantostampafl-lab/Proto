from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def test_market_lifecycle_is_computed_from_synthetic_research_inputs() -> None:
    response = client.get("/market-lifecycle")
    body = response.json()

    assert response.status_code == 200
    assert body["source"] == "SYNTHETIC_DEMO"
    assert body["count"] == 3
    assert {row["symbol"] for row in body["markets"]} == {"BTC", "ETH", "SOL"}
    for row in body["markets"]:
        assert row["resolution_state"] == "PENDING"
        assert row["real_money_execution"] is False
        assert row["expiry_horizon_minutes"] > 0
        assert 0.0 <= row["model_probability"] <= 1.0


def test_resolution_grid_never_claims_real_resolution() -> None:
    response = client.get("/resolution-grid")
    body = response.json()

    assert response.status_code == 200
    assert body["resolution_policy"] == "PENDING_SYNTHETIC_DEMO_ONLY"
    assert all(row["resolution_state"] == "PENDING" for row in body["markets"])


def test_expiry_map_exposes_data_axes_not_render_coordinates() -> None:
    response = client.get("/analytics/expiry-map")
    body = response.json()

    assert response.status_code == 200
    assert body["axes"] == {
        "radius": "expiry_horizon_minutes",
        "height": "model_probability",
        "intensity": "absolute_net_edge",
    }
    assert len(body["points"]) == 3
    for point in body["points"]:
        assert point["expiry_horizon_minutes"] > 0
        assert point["absolute_net_edge"] >= 0.0
