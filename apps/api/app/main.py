"""The single Proto API application: research, simulation, paper, live monitoring and dashboard.

`live_app.py` remains a separate, import-isolated read-only monitor for public deployments.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .agent_surface import router as agent_router
from .app_state import persistence_engine
from .core_routes import router as core_router
from .creation_surface import router as creation_router
from .equity_market_surface import router as equity_market_router
from .event_state import JournalUnavailable, operational_journal, record_operational_event
from .event_surface import router as event_router
from .live_routes import router as live_router
from .live_websockets import router as live_websocket_router
from .observability import access_log
from .oms_surface import router as oms_router
from .orchestration_surface import router as orchestration_router
from .paper_autonomy_bootstrap import router as paper_autonomy_router
from .paper_autopilot import router as paper_autopilot_router
from .paper_control import router as paper_control_router
from .persistence import init_database
from .research import metrics
from .research import router as research_router
from .risk_surface import router as risk_router
from .settings import settings
from .shadow_control import router as shadow_control_router
from .signal_surface import TRADINGVIEW_WEBHOOK_PATH
from .signal_surface import router as signal_router
from .universe_surface import router as universe_router

logger = logging.getLogger("proto.api")

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if persistence_engine is not None:
        await init_database(persistence_engine)
    await operational_journal.start()
    yield
    if persistence_engine is not None:
        await persistence_engine.dispose()

app = FastAPI(
    title="Prediction Market Quant Engine",
    version=__version__,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Request-ID", "X-Proto-Operator-Token"],
)

# The research router deliberately excludes /live so the live router and its lifespan
# are registered exactly once in this process.
for router in (
    research_router,
    core_router,
    live_router,
    live_websocket_router,
    event_router,
    paper_control_router,
    paper_autopilot_router,
    paper_autonomy_router,
    shadow_control_router,
    orchestration_router,
    universe_router,
    equity_market_router,
    creation_router,
    signal_router,
    agent_router,
    risk_router,
    oms_router,
):
    app.include_router(router)

@app.middleware("http")
async def observe_request(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        latency_ms = (perf_counter() - started) * 1_000
        metrics.record_http(path=request.url.path, status_code=500, latency_ms=latency_ms)
        logger.exception(
            access_log(
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                status_code=500,
                latency_ms=latency_ms,
            )
        )
        raise

    latency_ms = (perf_counter() - started) * 1_000
    metrics.record_http(
        path=request.url.path,
        status_code=response.status_code,
        latency_ms=latency_ms,
    )
    response.headers["X-Request-ID"] = request_id
    logger.info(
        access_log(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            latency_ms=latency_ms,
        )
    )
    return response

_DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"
_UI_SOURCE_DIGEST_FILE = _DASHBOARD_DIR / "proto-ui-source.sha256"
_ORCHESTRATION_SOURCE_FILE = Path(__file__).with_name("orchestration_surface.py")

_DASHBOARD_RELEASE = "proto-brain-control-plane-v3"
_DASHBOARD_UI_RELEASE = "operator-terminal-v4"
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
_GIT_COMMIT_SHA = os.getenv("PROTO_GIT_COMMIT_SHA", "").strip()
_GIT_BRANCH = os.getenv("PROTO_GIT_BRANCH", "").strip()
_DEPLOYMENT_ID = os.getenv("PROTO_DEPLOYMENT_ID", "").strip()
_ENVIRONMENT_NAME = os.getenv("PROTO_ENVIRONMENT", "").strip()

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

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CREATION_PREFIX = "/creation/"

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
async def operator_authentication_policy(request: Request, call_next) -> Response:
    """Fail closed for production state changes while preserving local testability.

    The Creation bridge and the TradingView webhook keep their own independent
    authentication (TradingView cannot send custom headers).
    """
    expected = settings.operator_api_token
    authentication_required = (
        settings.app_env.strip().lower() == "production" or expected is not None
    )
    mutating_operator_route = (
        request.method in _MUTATING_METHODS
        and not request.url.path.startswith(_CREATION_PREFIX)
        and request.url.path != TRADINGVIEW_WEBHOOK_PATH
    )
    if authentication_required and mutating_operator_route:
        if expected is None:
            return JSONResponse(
                status_code=503,
                content={"detail": "Operator authentication is not configured"},
            )
        supplied = request.headers.get("X-Proto-Operator-Token")
        if supplied is None or not hmac.compare_digest(supplied, expected):
            return JSONResponse(
                status_code=401,
                content={"detail": "Operator identity was not verified"},
            )
    return await call_next(request)

@app.middleware("http")
async def operational_audit_policy(request: Request, call_next) -> Response:
    """Record completed state-changing commands without inspecting private bodies."""
    response = await call_next(request)
    event_type = _OPERATIONAL_EVENTS.get(request.url.path) if request.method == "POST" else None
    if event_type is not None:
        try:
            await record_operational_event(
                event_type=event_type,
                source="proto-api",
                payload={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "request_id": response.headers.get("X-Request-ID"),
                    "financial_connectivity": False,
                    "real_money_execution": False,
                },
            )
        except JournalUnavailable:
            # The command already ran; flag the missing audit record instead of
            # turning its result into a misleading 500.
            metrics.increment("operational_journal_write_failures")
            logger.exception("operational audit event was not recorded for %s", request.url.path)
            response.headers["X-Proto-Audit"] = "unrecorded"
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
    if _GIT_COMMIT_SHA:
        response.headers["X-Proto-Git-Commit-SHA"] = _GIT_COMMIT_SHA
    if _DEPLOYMENT_ID:
        response.headers["X-Proto-Deployment-ID"] = _DEPLOYMENT_ID
    return response

@app.get("/deployment/info", tags=["system"])
def deployment_info() -> dict[str, object]:
    """Expose non-secret build provenance for operator and production verification."""
    return {
        "release": _DASHBOARD_RELEASE,
        "ui_release": _DASHBOARD_UI_RELEASE,
        "ui_source_sha256": _DASHBOARD_UI_SOURCE_SHA or None,
        "orchestration_source_sha256": _ORCHESTRATION_SOURCE_SHA or None,
        "git_commit_sha": _GIT_COMMIT_SHA or None,
        "git_branch": _GIT_BRANCH or None,
        "deployment_id": _DEPLOYMENT_ID or None,
        "environment": _ENVIRONMENT_NAME or None,
        "financial_connectivity": False,
        "real_money_execution": False,
    }

if _DASHBOARD_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_DASHBOARD_DIR, html=True), name="dashboard")
