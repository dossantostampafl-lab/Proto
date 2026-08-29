from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .event_surface import router as event_router
from .live_routes import router as live_router
from .security import (
    SlidingWindowRateLimiter,
    apply_security_headers,
    request_client_key,
)
from .settings import settings
from .websockets import hub

_rate_limiter = SlidingWindowRateLimiter(limit=settings.http_rate_limit_per_minute)

app = FastAPI(
    title="Proto Public Crypto Live Monitor",
    version=__version__,
    description=(
        "Standalone public read-only BTC/ETH/SOL market-data monitor. "
        "No financial accounts, credentials, order routing, or real-money execution."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Content-Type", "X-Request-ID"],
)
app.include_router(event_router)
app.include_router(live_router)


@app.middleware("http")
async def harden_http(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    if not _rate_limiter.allow(request_client_key(request)):
        response = JSONResponse(
            status_code=429,
            content={"detail": "rate limit exceeded"},
            headers={"Retry-After": "60"},
        )
    else:
        response = await call_next(request)

    response.headers["X-Request-ID"] = request_id
    apply_security_headers(
        response,
        secure_transport=request.url.scheme.lower() == "https",
    )
    return response


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "mode": "LIVE_MONITORING",
        "version": __version__,
        "source": "PUBLIC_READ_ONLY",
        "financial_connectivity": False,
        "real_money_execution": False,
    }


@app.websocket("/ws/market-data")
async def ws_market_data(websocket: WebSocket) -> None:
    await hub.serve("market-data", websocket)


@app.websocket("/ws/orderbook")
async def ws_orderbook(websocket: WebSocket) -> None:
    await hub.serve("orderbook", websocket)
