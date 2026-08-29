from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .event_surface import router as event_router
from .live_routes import router as live_router
from .live_websockets import router as live_websocket_router
from .security import SlidingWindowRateLimiter, apply_security_headers, request_client_key
from .settings import settings

app = FastAPI(
    title="Proto Public Crypto Monitor",
    description="Public unauthenticated BTC/ETH/SOL monitoring with no financial connectivity.",
    version=__version__,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Content-Type", "X-Request-ID"],
)

_rate_limiter = SlidingWindowRateLimiter(limit=settings.http_rate_limit_per_minute)


@app.middleware("http")
async def secure_read_only_http(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    started = perf_counter()
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        response = JSONResponse(
            status_code=405,
            content={"detail": "standalone live monitoring API is read-only"},
        )
    elif not _rate_limiter.allow(request_client_key(request)):
        response = JSONResponse(
            status_code=429,
            content={"detail": "rate limit exceeded"},
            headers={"Retry-After": "60"},
        )
    else:
        response = await call_next(request)

    apply_security_headers(response, secure_transport=request.url.scheme == "https")
    response.headers["X-Request-ID"] = request_id
    response.headers["Server-Timing"] = f"app;dur={(perf_counter() - started) * 1000:.3f}"
    return response


@app.get("/health", tags=["system"])
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "mode": "LIVE_MONITORING",
        "version": __version__,
        "source": "PUBLIC_READ_ONLY",
        "financial_connectivity": False,
        "real_money_execution": False,
    }


app.include_router(event_router)
app.include_router(live_router)
app.include_router(live_websocket_router)
