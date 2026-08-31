from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)

SAMPLE_COUNT = 64
BENCHMARK = [0.0] * SAMPLE_COUNT
STRONG = [
    0.015 + (0.002 if index % 4 == 0 else -0.001 if index % 4 == 1 else 0.0)
    for index in range(SAMPLE_COUNT)
]
WEAK = [0.0005 if index % 2 == 0 else -0.0004 for index in range(SAMPLE_COUNT)]
BAD = [
    -0.005 + (0.001 if index % 3 == 0 else 0.0)
    for index in range(SAMPLE_COUNT)
]


def _payload() -> dict[str, object]:
    return {
        "strategy_returns": [STRONG, WEAK, BAD],
        "benchmark_returns": BENCHMARK,
        "simulations": 300,
        "block_size": 4,
        "seed": 11,
    }


def test_data_snooping_endpoint_exposes_family_level_evidence() -> None:
    response = client.post("/research/validation/data-snooping", json=_payload())
    body = response.json()

    assert response.status_code == 200
    assert body["method"] == "WHITE_REALITY_CHECK_HANSEN_SPA"
    assert body["best_strategy_index"] == 0
    assert body["reality_check_p_value"] < 0.05
    assert body["spa_consistent_p_value"] < 0.05
    assert body["reality_check_p_value"] == body["spa_upper_p_value"]
    assert body["financial_connectivity"] is False
    assert body["real_money_execution"] is False


def test_data_snooping_endpoint_rejects_misaligned_evidence() -> None:
    payload = _payload()
    payload["strategy_returns"] = [STRONG, WEAK[:-1]]

    response = client.post("/research/validation/data-snooping", json=payload)

    assert response.status_code == 422


def test_data_snooping_endpoint_is_deterministic() -> None:
    first = client.post("/research/validation/data-snooping", json=_payload())
    second = client.post("/research/validation/data-snooping", json=_payload())

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
