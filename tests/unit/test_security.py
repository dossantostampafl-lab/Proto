from starlette.responses import Response

from apps.api.app.security import SlidingWindowRateLimiter, apply_security_headers


def test_sliding_window_rate_limiter_enforces_limit() -> None:
    now = 100.0

    def clock() -> float:
        return now

    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=10.0, clock=clock)

    assert limiter.allow("client") is True
    assert limiter.allow("client") is True
    assert limiter.allow("client") is False


def test_security_headers_are_fail_closed() -> None:
    response = Response()

    apply_security_headers(response, secure_transport=False)

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in response.headers


def test_hsts_is_only_added_for_secure_transport() -> None:
    response = Response()

    apply_security_headers(response, secure_transport=True)

    assert response.headers["Strict-Transport-Security"].startswith("max-age=31536000")
