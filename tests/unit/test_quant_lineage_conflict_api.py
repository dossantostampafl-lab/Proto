import pytest
from fastapi.testclient import TestClient

from apps.api.app import research, research_persistence
from apps.api.app.main import app


@pytest.mark.parametrize("route", ["pipeline", "replay"])
def test_lineage_identity_conflict_is_reported_as_409(monkeypatch, route):
    async def conflict(engine, result):
        raise research_persistence.QuantLineageCollision("identity conflict")

    monkeypatch.setattr(research, "persist_quant_lineage", conflict)
    frame = {
        "market_id": "btc",
        "symbol": "BTC",
        "observed_at": "2026-01-01T00:00:00Z",
        "market_probability": 0.5,
        "volatility": 0.2,
        "imbalance": 0.0,
        "liquidity_score": 0.9,
        "fees": 0.0,
        "slippage": 0.0,
        "spread_cost": 0.0,
        "hedge_cost": 0.0,
        "latency_penalty": 0.0,
    }
    body = frame if route == "pipeline" else {"frames": [frame]}
    response = TestClient(app, raise_server_exceptions=False).post(
        f"/research/quant/{route}", json=body
    )
    assert response.status_code == 409
