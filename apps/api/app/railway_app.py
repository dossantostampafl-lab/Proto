from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .live_routes import router as live_router
from .main import app
from .paper_autopilot import router as paper_autopilot_router
from .paper_control import router as paper_control_router

# Railway composes the public live surface at the application boundary. The
# research router deliberately excludes /live so the live router and its lifespan
# are registered exactly once in this process. Paper controls and the paper
# autopilot remain internal simulation-only surfaces.
app.include_router(live_router)
app.include_router(paper_control_router)
app.include_router(paper_autopilot_router)

# Static application-contract markers are advanced whenever the production
# backend or browser bundle contract changes. Production verification must wait
# for these exact values before accepting a healthy deployment, which prevents a
# stale Railway image from satisfying smoke/UI checks after a main-branch merge.
_DASHBOARD_RELEASE = "server-model-quality-v2"
_DASHBOARD_UI_RELEASE = "model-quality-persisted-v4"

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
    """Apply cache, browser-security, and release policy to Railway responses."""
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
    response.headers["X-Proto-Release"] = _DASHBOARD_RELEASE
    response.headers["X-Proto-UI-Release"] = _DASHBOARD_UI_RELEASE
    return response


_DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"

if _DASHBOARD_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=_DASHBOARD_DIR, html=True),
        name="dashboard",
    )
