from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from threading import Lock
from time import monotonic

from fastapi import Request, Response


class SlidingWindowRateLimiter:
    def __init__(
        self,
        *,
        limit: int = 600,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("rate limit must be positive")
        if window_seconds <= 0:
            raise ValueError("rate limit window must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def request_client_key(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host


def apply_security_headers(response: Response, *, secure_transport: bool) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    response.headers["Cache-Control"] = "no-store"
    if secure_transport:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
