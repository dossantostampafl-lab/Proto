from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def test_validation_report_exposes_research_metrics_and_safety_invariants() -> None:
    returns = [0.01, -0.004, 0.012, 0.003, -0.002, 0.009, 0.006, -0.003, 0.008, 0.004, -0.001, 0.007]
    response = client.post(
        "/research/validation/report",
        json={
            "returns": returns,
            "train_size": 6,
            "test_size": 3,
            "purge_size": 1,
            "embargo_size": 1,
            "trials": 20,
            "monte_carlo_simulations": 50,
            "monte_carlo_block_size": 2,
            "monte_carlo_seed": 13,
            "regimes": ["BULL"] * 4 + ["BEAR"] * 4 + ["SIDEWAYS"] * 4,
            "parameter_points": [
                {"parameter": 1.0, "score": 0.8},
                {"parameter": 1.5, "score": 1.0},
                {"parameter": 2.0, "score": 0.9},
            ],
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["fold_count"] >= 1
    assert 0.0 <= body["deflated_sharpe_ratio"] <= 1.0
    assert 0.0 <= body["monte_carlo"]["probability_of_loss"] <= 1.0
    assert body["regime"] is not None
    assert body["parameter_stability"] is not None
    assert body["financial_connectivity"] is False
    assert body["real_money_execution"] is False


def test_validation_report_serializes_infinite_ratio_metrics_as_null() -> None:
    response = client.post(
        "/research/validation/report",
        json={
            "returns": [0.01, 0.02, 0.015, 0.012, 0.011, 0.009, 0.013, 0.014],
            "train_size": 4,
            "test_size": 2,
            "monte_carlo_simulations": 20,
            "monte_carlo_block_size": 2,
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["performance"]["sortino"] is None
    assert body["performance"]["profit_factor"] is None


def test_pbo_endpoint_returns_bounded_probability() -> None:
    response = client.post(
        "/research/validation/pbo",
        json={
            "strategy_returns": [
                [0.02, 0.01, 0.015, 0.01, 0.018, 0.012, 0.014, 0.011],
                [-0.01, 0.003, -0.004, 0.002, -0.006, 0.001, -0.003, 0.002],
            ],
            "segments": 4,
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert 0.0 <= body["probability_of_backtest_overfitting"] <= 1.0
    assert body["strategy_count"] == 2
    assert body["real_money_execution"] is False


def test_pbo_rejects_odd_segment_count_at_contract_boundary() -> None:
    response = client.post(
        "/research/validation/pbo",
        json={
            "strategy_returns": [
                [0.01] * 8,
                [0.02] * 8,
            ],
            "segments": 5,
        },
    )

    assert response.status_code == 422
