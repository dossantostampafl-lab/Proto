from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def setup_function() -> None:
    client.post("/simulation/reset")


def test_calibration_endpoint_returns_research_metrics() -> None:
    response = client.post(
        "/research/calibration",
        json={
            "bins": 5,
            "observations": [
                {"probability": 0.8, "outcome": 1},
                {"probability": 0.2, "outcome": 0},
            ],
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["count"] == 2
    assert body["brier_score"] >= 0.0
    assert body["log_loss"] >= 0.0
    assert 0.0 <= body["expected_calibration_error"] <= 1.0


def test_historical_replay_orders_frames_by_timestamp() -> None:
    response = client.post(
        "/research/replay",
        json={
            "frames": [
                {
                    "timestamp": "2026-01-01T00:00:02Z",
                    "snapshot": {
                        "symbol": "BTC",
                        "market_id": "btc-replay",
                        "bid": 100.0,
                        "ask": 101.0,
                        "market_probability": 0.52,
                    },
                },
                {
                    "timestamp": "2026-01-01T00:00:01Z",
                    "snapshot": {
                        "symbol": "BTC",
                        "market_id": "btc-replay",
                        "bid": 99.0,
                        "ask": 100.0,
                        "market_probability": 0.51,
                    },
                },
            ]
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["mode"] == "HISTORICAL_REPLAY"
    assert body["processed_frames"] == 2
    assert body["finished"] is True
    assert body["frames"][0]["bid"] == 99.0
    assert body["frames"][1]["bid"] == 100.0


def test_metrics_track_research_requests() -> None:
    client.post(
        "/research/calibration",
        json={"observations": [{"probability": 0.75, "outcome": 1}]},
    )
    metrics = client.get("/research/metrics").json()

    assert metrics["counters"]["calibration_requests"] == 1
    assert metrics["latency_samples"] == 0
