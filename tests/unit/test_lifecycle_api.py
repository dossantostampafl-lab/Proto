import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import app
from apps.api.app.settings import settings

client = TestClient(app)


@pytest.fixture(autouse=True)
def enable_synthetic_research(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "synthetic_research_enabled", True)


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


def test_market_lifecycle_does_not_invent_execution_costs() -> None:
    response = client.get("/market-lifecycle")
    body = response.json()

    assert response.status_code == 200
    for row in body["markets"]:
        assert row["lifecycle_state"] == "ANALYZED"
        assert row["edge_decision"] == "COSTS_UNAVAILABLE"
        assert row["net_edge_is_partial"] is True
        assert row["cost_policy"] == "PARTIAL_DERIVED_COSTS_ONLY"
        assert set(row["known_costs"]) == {"spread_cost", "uncertainty_penalty"}
        assert set(row["unavailable_costs"]) == {
            "fees",
            "slippage",
            "hedge_cost",
            "latency_penalty",
        }
        assert row["known_costs"]["spread_cost"] >= 0.0
        assert row["known_costs"]["uncertainty_penalty"] >= 0.0


def test_resolution_grid_never_claims_real_resolution_or_complete_edge() -> None:
    response = client.get("/resolution-grid")
    body = response.json()

    assert response.status_code == 200
    assert body["resolution_policy"] == "PENDING_SYNTHETIC_DEMO_ONLY"
    assert all(row["resolution_state"] == "PENDING" for row in body["markets"])
    assert all(row["net_edge_is_partial"] is True for row in body["markets"])
    assert all(row["edge_decision"] == "COSTS_UNAVAILABLE" for row in body["markets"])


def test_expiry_map_exposes_partial_edge_provenance() -> None:
    response = client.get("/analytics/expiry-map")
    body = response.json()

    assert response.status_code == 200
    assert body["edge_policy"] == "PARTIAL_DERIVED_COSTS_ONLY"
    assert body["axes"] == {
        "radius": "expiry_horizon_minutes",
        "height": "model_probability",
        "intensity": "absolute_partial_net_edge",
    }
    assert len(body["points"]) == 3
    for point in body["points"]:
        assert point["expiry_horizon_minutes"] > 0
        assert point["net_edge_is_partial"] is True
        assert point["absolute_partial_net_edge"] >= 0.0
