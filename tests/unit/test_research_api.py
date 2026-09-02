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


def test_quant_pipeline_endpoint_exposes_safe_research_lineage() -> None:
    payload = {
        "market_id": "btc-threshold-research",
        "symbol": "BTC",
        "observed_at": "2026-08-31T12:00:00Z",
        "market_probability": 0.52,
        "volatility": 0.24,
        "imbalance": 0.35,
        "liquidity_score": 0.8,
        "fees": 0.0,
        "slippage": 0.0,
        "spread_cost": 0.0,
        "hedge_cost": 0.0,
        "latency_penalty": 0.0,
        "calibration_samples": [
            {"probability": 0.5, "outcome": 0},
            {"probability": 0.55, "outcome": 1},
        ],
        "event_times": [1788177598.0, 1788177599.0],
        "expiry_at": "2026-08-31T14:00:00Z",
    }

    first = client.post("/research/quant/pipeline", json=payload)
    second = client.post("/research/quant/pipeline", json=payload)
    body = first.json()

    assert first.status_code == 200
    assert second.status_code == 200
    assert body["correlation_id"] == second.json()["correlation_id"]
    assert body["persisted"] is False
    assert body["financial_connectivity"] is False
    assert body["real_money_execution"] is False
    assert 0.0 <= body["raw_probability"] <= 1.0
    assert 0.0 <= body["fair_probability"] <= 1.0
    assert body["model_version"]
    assert body["feature_version"]
    assert "liquidity_penalty" in body["edge"]
    assert body["calibration_report"]["count"] == 2


def test_quant_pipeline_rejects_naive_replay_clock() -> None:
    response = client.post(
        "/research/quant/pipeline",
        json={
            "market_id": "btc-threshold-research",
            "symbol": "BTC",
            "observed_at": "2026-08-31T12:00:00",
            "market_probability": 0.52,
            "volatility": 0.24,
            "imbalance": 0.35,
        },
    )

    assert response.status_code == 422


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
