from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def test_models_calibration_does_not_invent_metrics_without_persistence() -> None:
    response = client.get("/models/calibration")
    body = response.json()

    assert response.status_code == 200
    assert body["source"] == "PERSISTED_RESEARCH_LINEAGE"
    if body["status"] == "NOT_COMPUTED":
        assert body["observation_count"] == 0
        assert body["brier_score"] is None
        assert body["log_loss"] is None
        assert body["expected_calibration_error"] is None
        assert body["maximum_calibration_error"] is None
        assert body["reliability_curve"] == []
