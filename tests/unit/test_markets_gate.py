from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def test_markets_catalog_is_disabled_with_synthetic_surface_by_default() -> None:
    response = client.get("/markets")

    assert response.status_code == 503
    assert response.json()["detail"] == "synthetic research surface disabled"
