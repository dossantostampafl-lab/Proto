from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def _frame(observed_at: str) -> dict[str, object]:
    return {
        "market_id": "btc-replay",
        "symbol": "BTC",
        "observed_at": observed_at,
        "market_probability": 0.52,
        "volatility": 0.25,
        "imbalance": 0.10,
        "liquidity_score": 0.90,
        "fees": 0.0,
        "slippage": 0.0,
        "spread_cost": 0.0,
        "hedge_cost": 0.0,
        "latency_penalty": 0.0,
    }


def test_quant_replay_uses_only_strictly_past_calibration_observations() -> None:
    response = client.post(
        "/research/quant/replay",
        json={
            "frames": [
                _frame("2026-01-01T12:00:00+00:00"),
                _frame("2026-01-01T12:01:00+00:00"),
            ],
            "calibration_observations": [
                {
                    "observed_at": "2026-01-01T12:00:30+00:00",
                    "probability": 0.60,
                    "outcome": 1,
                },
                {
                    "observed_at": "2026-01-01T12:02:00+00:00",
                    "probability": 0.40,
                    "outcome": 0,
                },
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "HISTORICAL_REPLAY"
    assert body["processed_frames"] == 2
    assert body["calibration_policy"] == "STRICTLY_PAST_OBSERVATIONS_ONLY"
    assert body["financial_connectivity"] is False
    assert body["real_money_execution"] is False
    assert body["frames"][0]["calibration_report"] is None
    assert body["frames"][1]["calibration_report"]["count"] == 1


def test_quant_replay_rejects_out_of_order_frames() -> None:
    response = client.post(
        "/research/quant/replay",
        json={
            "frames": [
                _frame("2026-01-01T12:01:00+00:00"),
                _frame("2026-01-01T12:00:00+00:00"),
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "quant replay frames must be ordered by observed_at"
    )


def test_quant_replay_rejects_naive_calibration_timestamps() -> None:
    response = client.post(
        "/research/quant/replay",
        json={
            "frames": [_frame("2026-01-01T12:00:00+00:00")],
            "calibration_observations": [
                {
                    "observed_at": "2026-01-01T11:59:00",
                    "probability": 0.50,
                    "outcome": 1,
                }
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "calibration observation timestamps must be timezone-aware"
    )
