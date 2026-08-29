from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def setup_function() -> None:
    client.post("/simulation/reset")


def _replay_payload() -> dict[str, object]:
    return {
        "speed": "5x",
        "frames": [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "snapshot": {
                    "symbol": "BTC",
                    "market_id": "btc-demo",
                    "bid": 60_000,
                    "ask": 60_010,
                },
            },
            {
                "timestamp": "2026-01-01T00:00:01Z",
                "snapshot": {
                    "symbol": "BTC",
                    "market_id": "btc-demo",
                    "bid": 60_005,
                    "ask": 60_015,
                },
            },
        ],
    }


def test_documented_demo_control_plane_is_ready_and_simulation_only() -> None:
    health = client.get("/health")
    ready = client.get("/ready")
    risk = client.get("/risk")
    metrics = client.get("/metrics")

    assert health.status_code == 200
    assert ready.status_code == 200
    assert health.json()["mode"] in {
        "SIMULATION",
        "PAPER_TRADING",
        "HISTORICAL_REPLAY",
    }
    assert ready.json()["status"] == "ready"
    assert risk.json()["real_money_execution"] is False
    assert metrics.json()["real_money_execution"] is False


def test_documented_replay_demo_reaches_consistent_terminal_state() -> None:
    started = client.post("/replay/start", json=_replay_payload())
    assert started.status_code == 200
    assert started.json()["mode"] == "HISTORICAL_REPLAY"

    paused = client.post("/replay/pause")
    assert paused.status_code == 200

    seek = client.post("/replay/seek", json={"cursor": 1})
    assert seek.status_code == 200
    assert seek.json()["cursor"] == 1

    speed = client.post("/replay/speed", json={"speed": "100x"})
    assert speed.status_code == 200
    assert speed.json()["speed"] == "100x"

    stepped = client.post("/replay/step")
    assert stepped.status_code == 200
    assert stepped.json()["frame"]["market_id"] == "btc-demo"
    assert stepped.json()["finished"] is True

    reconciliation = client.get("/v1/reconciliation")
    assert reconciliation.status_code == 200
    assert reconciliation.json()["consistent"] is True

    triggered = client.post("/killswitch/trigger")
    assert triggered.status_code == 200
    assert triggered.json()["running"] is False
    assert client.get("/risk").json()["real_money_execution"] is False
