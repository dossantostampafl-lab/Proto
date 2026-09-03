from __future__ import annotations

import hashlib
import os
from pathlib import Path

from fastapi import Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .creation_surface import router as creation_router
from .equity_market_surface import router as equity_market_router
from .event_state import record_operational_event
from .event_surface import router as event_router
from .live_routes import router as live_router
from .main import app
from .orchestration_surface import router as orchestration_router
from .paper_autonomy_bootstrap import router as paper_autonomy_router
from .paper_autopilot import router as paper_autopilot_router
from .paper_control import router as paper_control_router
from .shadow_control import router as shadow_control_router
from .universe_surface import router as universe_router

# Railway composes the public live surface at the application boundary. The
# research router deliberately excludes /live so the live router and its lifespan
# are registered exactly once in this process. Paper and shadow controls remain
# simulation-only/non-financial surfaces. The event router exposes runtime health
# plus the append-only operational audit journal. The orchestration router is
# deliberately read-only: it exposes contracts/readiness but no endpoint capable
# of starting arbitrary jobs. The Creation bridge is separately authenticated and
# only maps allowlisted safe missions into ProtoBrain. Equity market-data routes
# remain read-only and expose no trading/order capability.
app.include_router(live_router)
app.include_router(event_router)
app.include_router(paper_control_router)
app.include_router(paper_autopilot_router)
app.include_router(paper_autonomy_router)
app.include_router(shadow_control_router)
app.include_router(orchestration_router)
app.include_router(universe_router)
app.include_router(equity_market_router)
app.include_router(creation_router)

_DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"
_UI_SOURCE_DIGEST_FILE = _DASHBOARD_DIR / "proto-ui-source.sha256"
_ORCHESTRATION_SOURCE_FILE = Path(__file__).with_name("orchestration_surface.py")

# Human-readable release names remain useful for operational rollouts, while the
# source digests prove which frontend/control-plane implementation is actually
# running. Railway-provided Git metadata gives us an independent deployment
# identity so a green provider status cannot be confused with the wrong service
# or an older image behind the public domain.
_DASHBOARD_RELEASE = "proto-brain-control-plane-v3"
_DASHBOARD_UI_RELEASE = "operator-terminal-v3"
_DASHBOARD_UI_SOURCE_SHA = (
    _UI_SOURCE_DIGEST_FILE.read_text(encoding="utf-8").strip()
    if _UI_SOURCE_DIGEST_FILE.is_file()
    else ""
)
_ORCHESTRATION_SOURCE_SHA = (
    hashlib.sha256(_ORCHESTRATION_SOURCE_FILE.read_bytes()).hexdigest()
    if _ORCHESTRATION_SOURCE_FILE.is_file()
    else ""
)
_RAILWAY_GIT_COMMIT_SHA = os.getenv("RAILWAY_GIT_COMMIT_SHA", "").strip()
_RAILWAY_GIT_BRANCH = os.getenv("RAILWAY_GIT_BRANCH", "").strip()
_RAILWAY_DEPLOYMENT_ID = os.getenv("RAILWAY_DEPLOYMENT_ID", "").strip()
_RAILWAY_SERVICE_ID = os.getenv("RAILWAY_SERVICE_ID", "").strip()
_RAILWAY_SERVICE_NAME = os.getenv("RAILWAY_SERVICE_NAME", "").strip()
_RAILWAY_ENVIRONMENT_NAME = os.getenv("RAILWAY_ENVIRONMENT_NAME", "").strip()

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
    "/shadow/start": "SHADOW_MODE_STARTED",
    "/shadow/stop": "SHADOW_MODE_STOPPED",
    "/shadow/evaluate": "SHADOW_DECISION_EVALUATED",
    "/creation/missions": "CREATION_MISSION_SUBMITTED",
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
    """Apply cache, browser-security, release and deployment provenance policy."""
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
    if _DASHBOARD_UI_SOURCE_SHA:
        response.headers["X-Proto-UI-Source-SHA256"] = _DASHBOARD_UI_SOURCE_SHA
    if _ORCHESTRATION_SOURCE_SHA:
        response.headers["X-Proto-Orchestration-Source-SHA256"] = _ORCHESTRATION_SOURCE_SHA
    if _RAILWAY_GIT_COMMIT_SHA:
        response.headers["X-Proto-Git-Commit-SHA"] = _RAILWAY_GIT_COMMIT_SHA
    if _RAILWAY_DEPLOYMENT_ID:
        response.headers["X-Proto-Deployment-ID"] = _RAILWAY_DEPLOYMENT_ID
    return response


@app.get("/deployment/info", tags=["system"])
def deployment_info() -> dict[str, object]:
    """Expose non-secret build provenance for operator and production verification."""
    return {
        "release": _DASHBOARD_RELEASE,
        "ui_release": _DASHBOARD_UI_RELEASE,
        "ui_source_sha256": _DASHBOARD_UI_SOURCE_SHA or None,
        "orchestration_source_sha256": _ORCHESTRATION_SOURCE_SHA or None,
        "git_commit_sha": _RAILWAY_GIT_COMMIT_SHA or None,
        "git_branch": _RAILWAY_GIT_BRANCH or None,
        "deployment_id": _RAILWAY_DEPLOYMENT_ID or None,
        "service_id": _RAILWAY_SERVICE_ID or None,
        "service_name": _RAILWAY_SERVICE_NAME or None,
        "environment": _RAILWAY_ENVIRONMENT_NAME or None,
        "financial_connectivity": False,
        "real_money_execution": False,
    }


if _DASHBOARD_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=_DASHBOARD_DIR, html=True),
        name="dashboard",
    )
