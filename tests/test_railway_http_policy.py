from fastapi.testclient import TestClient

from apps.api.app.railway_app import _DASHBOARD_RELEASE, app


def test_railway_http_policy_exposes_security_and_release_headers() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["x-proto-release"] == _DASHBOARD_RELEASE
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "connect-src 'self' ws: wss:" in response.headers["content-security-policy"]
    assert "payment=()" in response.headers["permissions-policy"]
