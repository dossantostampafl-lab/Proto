from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient

from apps.api.app.main import app
from services.validation.experiments import stable_fingerprint

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


def _payload() -> dict[str, object]:
    return {
        "manifest": {
            "research_mode": "HISTORICAL_REPLAY",
            "dataset": {
                "name": "coinbase-btc-eth-sol-l2-2026-08-01",
                "source": "coinbase_advanced_trade_public_ws",
                "venue": "COINBASE",
                "data_level": "L2",
                "content_sha256": "A" * 64,
                "schema_version": "l2-normalized-v1",
                "symbols": ["SOL-USD", "BTC-USD", "ETH-USD", "BTC-USD"],
                "start_at": "2026-08-01T00:00:00+00:00",
                "end_at": "2026-08-02T00:00:00+00:00",
                "event_count": 100_000,
                "quality": {"gaps": 0, "sequence_valid": True},
            },
            "feature_version": "features-v1",
            "strategy_name": "constant-spread-baseline",
            "strategy_version": "v1",
            "model_version": "none",
            "git_sha": "DD73011C7D1977A1A08FC052167EA7F1872911E4",
            "seed": 13,
            "replay_fingerprint": "B" * 64,
            "windows": [
                {
                    "role": "TRAIN",
                    "start_at": "2026-08-01T00:00:00+00:00",
                    "end_at": "2026-08-01T12:00:00+00:00",
                },
                {
                    "role": "OOS",
                    "start_at": "2026-08-01T12:00:00+00:00",
                    "end_at": "2026-08-02T00:00:00+00:00",
                },
            ],
            "parameters": {"half_spread_bps": 5.0},
            "execution_assumptions": {
                "latency_ms": 10.0,
                "fee_bps": 4.0,
                "queue_model": "L2_AHEAD",
            },
        },
        "validation": {
            "returns": RETURNS,
            "train_size": 6,
            "test_size": 3,
            "purge_size": 1,
            "embargo_size": 1,
            "trials": 20,
            "monte_carlo_simulations": 50,
            "monte_carlo_block_size": 2,
            "monte_carlo_seed": 13,
        },
    }


def test_fingerprint_is_stable_across_mapping_order() -> None:
    left = {"dataset": {"source": "coinbase", "events": 10}, "seed": 7}
    right = {"seed": 7, "dataset": {"events": 10, "source": "coinbase"}}
    assert stable_fingerprint(left) == stable_fingerprint(right)


def test_validation_experiment_is_deterministic_and_normalizes_provenance() -> None:
    first = client.post("/research/validation/experiments/validate", json=_payload())
    second = client.post("/research/validation/experiments/validate", json=_payload())

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["experiment_id"] == second_body["experiment_id"]
    assert first_body["dataset_fingerprint"] == second_body["dataset_fingerprint"]
    assert first_body["returns_fingerprint"] == second_body["returns_fingerprint"]
    assert first_body["trial_family_fingerprint"] is None
    assert first_body["validation_result"] == second_body["validation_result"]
    assert len(first_body["experiment_id"]) == 64
    assert first_body["manifest"]["dataset"]["content_sha256"] == "a" * 64
    assert first_body["manifest"]["dataset"]["symbols"] == [
        "BTC-USD",
        "ETH-USD",
        "SOL-USD",
    ]
    assert first_body["manifest"]["git_sha"] == (
        "dd73011c7d1977a1a08fc052167ea7f1872911e4"
    )
    assert first_body["financial_connectivity"] is False
    assert first_body["real_money_execution"] is False


def test_dataset_content_changes_experiment_identity() -> None:
    first = _payload()
    second = deepcopy(first)
    second["manifest"]["dataset"]["content_sha256"] = "c" * 64

    first_response = client.post(
        "/research/validation/experiments/validate",
        json=first,
    )
    second_response = client.post(
        "/research/validation/experiments/validate",
        json=second,
    )
    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["experiment_id"] != second_response.json()["experiment_id"]


def test_returns_are_evidence_not_part_of_experiment_identity() -> None:
    first = _payload()
    second = deepcopy(first)
    second["validation"]["returns"] = [value * 0.5 for value in RETURNS]

    first_response = client.post(
        "/research/validation/experiments/validate",
        json=first,
    )
    second_response = client.post(
        "/research/validation/experiments/validate",
        json=second,
    )
    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["experiment_id"] == second_response.json()["experiment_id"]
    assert (
        first_response.json()["returns_fingerprint"]
        != second_response.json()["returns_fingerprint"]
    )


def test_trial_family_is_fingerprinted_as_validation_evidence() -> None:
    without_family = _payload()
    with_family = deepcopy(without_family)
    with_family["validation"]["trial_returns"] = [RETURNS, RETURNS, RETURNS]

    first_response = client.post(
        "/research/validation/experiments/validate",
        json=without_family,
    )
    second_response = client.post(
        "/research/validation/experiments/validate",
        json=with_family,
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first_body = first_response.json()
    second_body = second_response.json()
    assert first_body["experiment_id"] == second_body["experiment_id"]
    assert first_body["trial_family_fingerprint"] is None
    assert len(second_body["trial_family_fingerprint"]) == 64
    assert "trial_returns" not in second_body["validation_plan"]
    assert second_body["validation_result"]["dsr_trials"] == 1
    assert second_body["validation_result"]["trial_accounting"][
        "effective_independent_trials"
    ] == 1


def test_dataset_requires_timezone_aware_bounds() -> None:
    payload = _payload()
    payload["manifest"]["dataset"]["start_at"] = "2026-08-01T00:00:00"
    response = client.post(
        "/research/validation/experiments/validate",
        json=payload,
    )
    assert response.status_code == 422


def test_experiment_windows_must_be_non_overlapping_and_inside_dataset() -> None:
    payload = _payload()
    payload["manifest"]["windows"][1]["start_at"] = "2026-08-01T11:59:00+00:00"
    response = client.post(
        "/research/validation/experiments/validate",
        json=payload,
    )
    assert response.status_code == 422
