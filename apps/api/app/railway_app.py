from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .live_routes import router as live_router
from .main import app
from .paper_control import router as paper_control_router

# Railway runs the research/simulation API and the public read-only BTC/ETH/SOL
# monitor in the same process. The live router owns the monitor lifespan and uses
# the existing WebSocket hub from ``main`` to publish market-data/orderbook
# frames. Paper controls only switch the internal simulated runtime and never add
# account credentials or financial connectivity.
app.include_router(live_router)
app.include_router(paper_control_router)

_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "connect-src 'self' ws: wss:",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    )
)


@app.middleware("http")
async def dashboard_http_policy(request: Request, call_next) -> Response:
    """Apply cache and browser-security policy to the Railway web surface."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    elif path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"

    response.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    return response


_DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"

if _DASHBOARD_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=_DASHBOARD_DIR, html=True),
        name="dashboard",
    )
