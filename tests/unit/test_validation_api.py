from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)

RETURNS = [
    0.01,
    -0.004,
    0.012,
    0.003,
    -0.002,
    0.009,
    0.006,
    -0.003,
    0.008,
    0.004,
    -0.001,
    0.007,
]


def test_validation_report_exposes_research_metrics_and_safety_invariants() -> None:
    response = client.post(
        "/research/validation/report",
        json={
            "returns": RETURNS,
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
    assert body["dsr_trials"] == 20
    assert body["trial_accounting"]["method"] == "declared_trials"
    assert body["trial_accounting"]["effective_independent_trials"] == 20
    assert 0.0 <= body["monte_carlo"]["probability_of_loss"] <= 1.0
    assert body["regime"] is not None
    assert body["parameter_stability"] is not None
    assert body["financial_connectivity"] is False
    assert body["real_money_execution"] is False


def test_validation_report_uses_correlation_adjusted_trial_burden_for_dsr() -> None:
    response = client.post(
        "/research/validation/report",
        json={
            "returns": RETURNS,
            "train_size": 6,
            "test_size": 3,
            "trials": 99,
            "trial_returns": [RETURNS, RETURNS, RETURNS],
            "monte_carlo_simulations": 20,
            "monte_carlo_block_size": 2,
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["dsr_trials"] == 1
    assert body["trial_accounting"]["declared_trials"] == 3
    assert body["trial_accounting"]["effective_independent_trials"] == 1
    assert body["trial_accounting"]["average_pairwise_correlation"] == 1.0
    assert body["trial_accounting"]["method"] == "average_pairwise_correlation"


def test_effective_trials_endpoint_exposes_search_burden_and_safety_flags() -> None:
    response = client.post(
        "/research/validation/trials/effective",
        json={"trial_returns": [RETURNS, RETURNS, RETURNS]},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["trial_accounting"]["declared_trials"] == 3
    assert body["trial_accounting"]["effective_independent_trials"] == 1
    assert body["trial_accounting"]["pair_count"] == 3
    assert body["financial_connectivity"] is False
    assert body["real_money_execution"] is False


def test_effective_trials_endpoint_rejects_unusable_correlation_evidence() -> None:
    response = client.post(
        "/research/validation/trials/effective",
        json={"trial_returns": [RETURNS, [0.01] * len(RETURNS)]},
    )

    assert response.status_code == 422
    assert "non-zero variance" in response.json()["detail"]


def test_validation_report_rejects_trial_family_with_different_sample_length() -> None:
    response = client.post(
        "/research/validation/report",
        json={
            "returns": RETURNS,
            "train_size": 6,
            "test_size": 3,
            "trial_returns": [RETURNS[:-1]],
            "monte_carlo_simulations": 20,
            "monte_carlo_block_size": 2,
        },
    )

    assert response.status_code == 422


def test_validation_report_serializes_infinite_ratio_metrics_as_null() -> None:
    response = client.post(
        "/research/validation/report",
        json={
            "returns": [
                0.01,
                0.02,
                0.015,
                0.012,
                0.011,
                0.009,
                0.013,
                0.014,
            ],
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


def test_validation_report_invalid_fold_geometry_returns_422() -> None:
    response = client.post(
        "/research/validation/report",
        json={
            "returns": [0.01, 0.02, -0.01, 0.005, 0.004, -0.002],
            "train_size": 6,
            "test_size": 3,
            "monte_carlo_simulations": 20,
            "monte_carlo_block_size": 2,
        },
    )
    body = response.json()

    assert response.status_code == 422
    assert "no validation folds" in body["detail"]


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
