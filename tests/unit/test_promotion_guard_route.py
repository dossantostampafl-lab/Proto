from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def _payload() -> dict[str, object]:
    return {
        "evidence": {
            "experiment_id": "a" * 64,
            "candidate_kind": "ALPHA_CANDIDATE",
            "oos_sample_count": 500,
            "validation_fold_count": 8,
            "cumulative_return": 0.12,
            "sharpe": 0.80,
            "max_drawdown": 0.08,
            "positive_fold_fraction": 0.75,
            "robustness_score": 0.82,
            "deflated_sharpe_ratio": 0.98,
            "probability_of_backtest_overfitting": 0.10,
            "monte_carlo_probability_of_loss": 0.20,
            "regime_robustness_score": 0.75,
            "parameter_stability_score": 0.70,
            "delay_control_sharpe": 0.20,
            "shuffle_control_sharpe": 0.10,
            "family_reality_check_p_value": 0.01,
            "family_spa_p_value": 0.01,
            "frozen_holdout_passed": True,
            "frozen_holdout_consumed": True,
            "frozen_holdout_seal_id": "holdout-seal-1",
        }
    }


def test_public_promotion_route_uses_family_and_holdout_guard() -> None:
    response = client.post("/research/validation/promotion/evaluate", json=_payload())
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "PAPER_TRADING_ELIGIBLE"
    assert len(body["checks"]) == 19
    assert body["live_execution_eligible"] is False
    assert body["real_money_execution"] is False


def test_public_promotion_route_fails_closed_without_holdout_evidence() -> None:
    payload = _payload()
    evidence = payload["evidence"]
    assert isinstance(evidence, dict)
    evidence["frozen_holdout_passed"] = False
    evidence["frozen_holdout_consumed"] = False
    evidence["frozen_holdout_seal_id"] = None

    response = client.post("/research/validation/promotion/evaluate", json=payload)
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "RESEARCH_ONLY"
    assert "frozen_holdout_passed" in body["failed_checks"]
    assert "frozen_holdout_consumed" in body["failed_checks"]
    assert "frozen_holdout_seal_id" in body["failed_checks"]
