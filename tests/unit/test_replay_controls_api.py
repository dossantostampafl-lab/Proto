from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def _start_replay() -> None:
    client.post("/simulation/reset")
    response = client.post(
        "/replay/start",
        json={
            "speed": "5x",
            "frames": [
                {
                    "timestamp": f"2026-01-01T00:00:0{second}Z",
                    "snapshot": {
                        "symbol": "BTC",
                        "market_id": "btc-replay",
                        "bid": 100 + second,
                        "ask": 101 + second,
                    },
                }
                for second in range(3)
            ],
        },
    )
    assert response.status_code == 200


def test_seek_speed_and_reset_complete_replay_controls() -> None:
    _start_replay()

    seek = client.post("/replay/seek", json={"cursor": 2})
    speed = client.post("/replay/speed", json={"speed": "100x"})
    reset = client.post("/replay/reset")

    assert seek.status_code == 200
    assert seek.json()["cursor"] == 2
    assert seek.json()["paused"] is True
    assert speed.status_code == 200
    assert speed.json()["speed"] == "100x"
    assert reset.status_code == 200
    assert reset.json()["active"] is False
    assert reset.json()["mode"] == "HISTORICAL_REPLAY"


def test_seek_rejects_cursor_beyond_dataset() -> None:
    _start_replay()

    response = client.post("/replay/seek", json={"cursor": 4})

    assert response.status_code == 409
    assert response.json()["detail"] == "replay cursor exceeds total frames"

