from fastapi.testclient import TestClient

from apps.api.app.main import app


def test_api_surface_sets_browser_security_headers() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "connect-src 'self' ws: wss:" in response.headers["content-security-policy"]
    assert "payment=()" in response.headers["permissions-policy"]
