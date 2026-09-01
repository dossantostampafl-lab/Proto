from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def _seal_payload() -> dict[str, object]:
    return {
        "experiment_id": "a" * 64,
        "dataset_content_sha256": "b" * 64,
        "holdout_start_at": "2026-01-01T00:00:00Z",
        "holdout_end_at": "2026-02-01T00:00:00Z",
        "feature_version": "features-v1",
        "strategy_name": "trend-specialist",
        "strategy_version": "1.0.0",
        "model_version": "model-v1",
        "git_sha": "abc1234",
        "parameters": {"lookback": 20},
        "execution_assumptions": {"fee_bps": 5},
    }


def test_frozen_holdout_seal_route_exists_and_fails_closed_without_persistence() -> None:
    response = client.post("/research/validation/holdout/seal", json=_seal_payload())

    assert response.status_code == 503
    assert response.json()["detail"] == "frozen holdout requires durable persistence"


def test_frozen_holdout_evaluate_route_exists_and_fails_closed_without_persistence() -> None:
    payload = {
        **_seal_payload(),
        "seal_id": "c" * 64,
        "returns": [0.01, 0.005],
        "policy": {
            "min_samples": 2,
            "min_cumulative_return": 0.0,
            "min_sharpe": 0.0,
            "max_drawdown": 0.2,
        },
    }
    response = client.post("/research/validation/holdout/evaluate", json=payload)

    assert response.status_code == 503
    assert response.json()["detail"] == "frozen holdout requires durable persistence"
