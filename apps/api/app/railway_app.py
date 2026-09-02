from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .event_state import record_operational_event
from .event_surface import router as event_router
from .live_routes import router as live_router
from .main import app
from .orchestration_surface import router as orchestration_router
from .paper_autopilot import router as paper_autopilot_router
from .paper_control import router as paper_control_router

# Railway composes the public live surface at the application boundary. The
# research router deliberately excludes /live so the live router and its lifespan
# are registered exactly once in this process. Paper controls and the paper
# autopilot remain internal simulation-only surfaces. The event router exposes
# runtime health plus the append-only operational audit journal. The orchestration
# router is deliberately read-only: it exposes contracts/readiness but no endpoint
# capable of starting arbitrary jobs.
app.include_router(live_router)
app.include_router(event_router)
app.include_router(paper_control_router)
app.include_router(paper_autopilot_router)
app.include_router(orchestration_router)

# Deliberately static application-contract markers. Production verification uses
# them to distinguish "the endpoint responds" from "the expected backend/UI
# generation is actually deployed" without provider-specific Railway metadata.
_DASHBOARD_RELEASE = "proto-brain-control-plane-v1"
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

_OPERATIONAL_EVENTS = {
    "/simulation/start": "SIMULATION_STARTED",
    "/simulation/stop": "SIMULATION_STOPPED",
    "/simulation/reset": "SIMULATION_RESET",
    "/v1/simulate": "SIMULATION_REQUEST_PROCESSED",
    "/v1/portfolio/mark": "PORTFOLIO_MARK_UPDATED",
    "/killswitch/trigger": "KILL_SWITCH_TRIGGERED",
    "/killswitch/reset": "KILL_SWITCH_RESET",
    "/replay/start": "REPLAY_STARTED",
    "/replay/pause": "REPLAY_PAUSED",
    "/replay/resume": "REPLAY_RESUMED",
    "/replay/step": "REPLAY_STEPPED",
    "/replay/restart": "REPLAY_RESTARTED",
    "/replay/seek": "REPLAY_SEEKED",
    "/replay/speed": "REPLAY_SPEED_UPDATED",
    "/replay/reset": "REPLAY_RESET",
    "/paper/start": "PAPER_TRADING_STARTED",
    "/paper/stop": "PAPER_TRADING_STOPPED",
    "/paper/automation/start": "PAPER_AUTOPILOT_STARTED",
    "/paper/automation/stop": "PAPER_AUTOPILOT_STOPPED",
}


@app.middleware("http")
async def operational_audit_policy(request: Request, call_next) -> Response:
    """Record completed state-changing commands without inspecting private bodies."""
    response = await call_next(request)
    event_type = _OPERATIONAL_EVENTS.get(request.url.path) if request.method == "POST" else None
    if event_type is not None:
        await record_operational_event(
            event_type=event_type,
            source="railway-api",
            payload={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "request_id": response.headers.get("X-Request-ID"),
                "financial_connectivity": False,
                "real_money_execution": False,
            },
        )
    return response


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
