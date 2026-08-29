from apps.api.app.demo import DemoEngine
from apps.api.app.main import app
from fastapi.testclient import TestClient


client = TestClient(app)


def test_demo_engine_is_deterministic_after_reset() -> None:
    engine = DemoEngine()
    first = engine.next_frame()
    engine.next_frame()
    engine.reset()
    replayed = engine.next_frame()

    assert first["sequence"] == 1
    assert replayed["sequence"] == 1
    assert first["model_feed"] == replayed["model_feed"]
    assert first["resolution_grid"] == replayed["resolution_grid"]


def test_demo_frame_is_explicitly_synthetic() -> None:
    frame = DemoEngine().next_frame()

    assert frame["source"] == "SYNTHETIC_DETERMINISTIC"
    assert frame["real_market_data"] is False
    assert frame["real_money_execution"] is False
    assert {item["symbol"] for item in frame["model_feed"]} == {"BTC", "ETH", "SOL"}
    assert len(frame["resolution_grid"]) == 12

    for feed in frame["model_feed"]:
        assert 0.0 <= feed["market_probability"] <= 1.0
        assert 0.0 <= feed["model_probability"] <= 1.0
        assert 0.0 <= feed["confidence"] <= 1.0
        assert feed["source"] == "SYNTHETIC_DETERMINISTIC"


def test_demo_api_reset_tick_and_continuous_lifecycle() -> None:
    reset_response = client.post("/research/demo/reset")
    assert reset_response.status_code == 200
    assert reset_response.json()["sequence"] == 0
    assert reset_response.json()["running"] is False

    tick_response = client.post("/research/demo/tick")
    body = tick_response.json()

    assert tick_response.status_code == 200
    assert body["sequence"] == 1
    assert len(body["model_feed"]) == 3
    assert len(body["resolution_grid"]) == 12
    assert body["real_market_data"] is False

    start_response = client.post("/research/demo/start?interval_ms=100")
    assert start_response.status_code == 200
    assert start_response.json()["running"] is True
    assert start_response.json()["interval_ms"] == 100

    stop_response = client.post("/research/demo/stop")
    assert stop_response.status_code == 200
    assert stop_response.json()["running"] is False
