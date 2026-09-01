from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def _inputs(**overrides: float) -> dict[str, float]:
    payload = {
        "capital": 100_000.0,
        "max_fraction": 0.02,
        "volatility": 0.20,
        "target_volatility": 0.15,
        "net_edge": 0.05,
        "confidence": 0.80,
        "market_probability": 0.50,
        "model_probability": 0.60,
        "hard_notional_cap": 10_000.0,
    }
    payload.update(overrides)
    return payload


def test_fixed_fractional_position_sizing_is_deterministic() -> None:
    request = {
        "method": "FIXED_FRACTIONAL",
        "inputs": _inputs(),
    }

    first = client.post("/research/portfolio/size", json=request)
    second = client.post("/research/portfolio/size", json=request)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["fraction"] == 0.02
    assert first.json()["notional"] == 2_000.0
    assert first.json()["capped"] is False
    assert first.json()["research_only"] is False
    assert first.json()["real_money_execution"] is False


def test_hard_notional_cap_is_enforced() -> None:
    response = client.post(
        "/research/portfolio/size",
        json={
            "method": "FIXED_FRACTIONAL",
            "inputs": _inputs(hard_notional_cap=1_500.0),
        },
    )

    assert response.status_code == 200
    assert response.json()["notional"] == 1_500.0
    assert response.json()["capped"] is True
    assert response.json()["hard_notional_cap"] == 1_500.0


def test_edge_adjusted_sizing_uses_edge_and_confidence() -> None:
    response = client.post(
        "/research/portfolio/size",
        json={
            "method": "EDGE_ADJUSTED",
            "inputs": _inputs(),
        },
    )

    assert response.status_code == 200
    assert response.json()["fraction"] == 0.008
    assert response.json()["notional"] == 800.0


def test_capped_kelly_remains_explicitly_research_only() -> None:
    response = client.post(
        "/research/portfolio/size",
        json={
            "method": "CAPPED_KELLY_RESEARCH_ONLY",
            "inputs": _inputs(),
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["fraction"] == 0.02
    assert body["notional"] == 2_000.0
    assert body["research_only"] is True
    assert body["financial_connectivity"] is False
    assert body["real_money_execution"] is False


def test_position_sizing_rejects_invalid_contract_probability() -> None:
    response = client.post(
        "/research/portfolio/size",
        json={
            "method": "CAPPED_KELLY_RESEARCH_ONLY",
            "inputs": _inputs(market_probability=1.0),
        },
    )

    assert response.status_code == 422
