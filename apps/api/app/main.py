from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from services.quant.core import (
    EdgeBreakdown,
    ProbabilityEstimate,
    compute_edge,
    estimate_probability,
)

from . import __version__
from .models import (
    KillSwitchState,
    MarketSnapshot,
    PortfolioMarkRequest,
    RuntimeState,
    SimulationRequest,
    SimulationResult,
    SystemMode,
)
from .observability import LatencyTimer, access_log
from .persistence import AsyncSqlFillJournal, build_engine, database_ready, init_database
from .portfolio import PaperPortfolio
from .replay import ReplaySession, ReplayStartRequest
from .research import metrics
from .research import router as research_router
from .settings import settings
from .simulation import PaperSimulator
from .websockets import hub

logger = logging.getLogger("proto.api")
persistence_engine = build_engine(settings.database_url) if settings.persistence_enabled else None
persistent_journal = (
    AsyncSqlFillJournal(persistence_engine) if persistence_engine is not None else None
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if persistence_engine is not None:
        await init_database(persistence_engine)
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
    allow_headers=["Content-Type", "X-Request-ID"],
)
app.include_router(research_router)

runtime = RuntimeState()
simulator = PaperSimulator()
portfolio = PaperPortfolio()
replay_session = ReplaySession()


@app.middleware("http")
async def observe_request(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        latency_ms = (perf_counter() - started) * 1_000
        metrics.record_http(
            path=request.url.path,
            status_code=500,
            latency_ms=latency_ms,
        )
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


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "mode": runtime.mode,
        "version": __version__,
        "persistence_enabled": settings.persistence_enabled,
    }


@app.get("/ready")
async def ready(response: Response) -> dict[str, object]:
    if persistence_engine is None:
        return {
            "status": "ready",
            "database": "disabled",
            "mode": runtime.mode,
        }
    ready_state = await database_ready(persistence_engine)
    if not ready_state:
        response.status_code = 503
    return {
        "status": "ready" if ready_state else "not_ready",
        "database": "ready" if ready_state else "unavailable",
        "mode": runtime.mode,
    }


@app.get("/metrics")
def system_metrics() -> dict[str, object]:
    return {
        "mode": runtime.mode,
        "real_money_execution": False,
        **metrics.snapshot(),
    }


@app.get("/system/status", response_model=RuntimeState)
def system_status() -> RuntimeState:
    return runtime


@app.get("/markets")
def markets() -> list[dict[str, str]]:
    return [
        {"id": "btc-threshold", "asset": "BTC", "state": "ANALYZED"},
        {"id": "eth-threshold", "asset": "ETH", "state": "ANALYZED"},
        {"id": "sol-threshold", "asset": "SOL", "state": "ANALYZED"},
    ]


@app.post("/probability/estimate", response_model=ProbabilityEstimate)
def probability(snapshot: MarketSnapshot) -> ProbabilityEstimate:
    metrics.increment("probability_requests")
    return estimate_probability(
        market_probability=snapshot.market_probability,
        volatility=snapshot.volatility,
        imbalance=snapshot.imbalance,
    )


@app.post("/edge/evaluate", response_model=EdgeBreakdown)
def edge(snapshot: MarketSnapshot) -> EdgeBreakdown:
    metrics.increment("edge_requests")
    estimate = probability(snapshot)
    spread_cost = max(snapshot.ask - snapshot.bid, 0.0) / max(snapshot.ask + snapshot.bid, 1e-9)
    return compute_edge(
        model_probability=estimate.probability,
        market_probability=snapshot.market_probability,
        fees=0.001,
        slippage=0.001,
        spread_cost=spread_cost,
        hedge_cost=0.001,
        uncertainty_penalty=estimate.uncertainty * 0.02,
        latency_penalty=0.0005,
        minimum_edge=settings.minimum_net_edge,
    )


@app.get("/risk")
def risk() -> dict[str, object]:
    return {
        "kill_switch": runtime.kill_switch,
        "simulation_allowed": runtime.running
        and runtime.kill_switch == KillSwitchState.ARMED,
        "real_money_execution": False,
        "minimum_net_edge": settings.minimum_net_edge,
        "minimum_confidence": settings.minimum_confidence,
        "max_notional": settings.max_notional,
        "max_daily_drawdown": settings.max_daily_drawdown,
    }


@app.post("/v1/simulate", response_model=SimulationResult)
async def simulate(request: SimulationRequest) -> SimulationResult:
    metrics.increment("simulation_requests")
    if not runtime.running or runtime.kill_switch != KillSwitchState.ARMED:
        metrics.increment("simulation_rejected")
        return SimulationResult(accepted=False, reason="simulation halted")

    with LatencyTimer(metrics):
        result = simulator.simulate(request)

    if result.accepted and result.fill is not None:
        metrics.increment("simulation_accepted")
        portfolio.apply_fill(request.order, result.fill)
        if persistent_journal is not None:
            await persistent_journal.append(request.order, result.fill)
        await hub.broadcast(
            "fills",
            {"type": "fill", "data": result.fill.model_dump(mode="json")},
        )
        await hub.broadcast(
            "portfolio",
            {"type": "portfolio", "data": portfolio.snapshot()},
        )
    else:
        metrics.increment("simulation_rejected")
    return result


@app.get("/v1/portfolio")
def get_portfolio() -> dict[str, object]:
    return portfolio.snapshot()


@app.post("/v1/portfolio/mark")
async def mark_portfolio(request: PortfolioMarkRequest) -> dict[str, object]:
    marks = {mark.asset: mark.price for mark in request.marks}
    snapshot = portfolio.snapshot(marks)
    await hub.broadcast("portfolio", {"type": "portfolio", "data": snapshot})
    return snapshot


@app.get("/v1/fills")
async def get_fills(limit: int = Query(default=100, ge=1, le=1_000)) -> dict[str, object]:
    entries = (
        await persistent_journal.list(limit)
        if persistent_journal is not None
        else portfolio.journal(limit)
    )
    return {"mode": SystemMode.SIMULATION, "count": len(entries), "fills": entries}


@app.post("/simulation/start", response_model=RuntimeState)
async def simulation_start() -> RuntimeState:
    if runtime.kill_switch != KillSwitchState.ARMED:
        runtime.running = False
        return runtime
    replay_session.reset()
    runtime.mode = SystemMode.SIMULATION
    runtime.running = True
    await hub.broadcast(
        "analytics",
        {"type": "runtime", "data": runtime.model_dump(mode="json")},
    )
    return runtime


@app.post("/simulation/stop", response_model=RuntimeState)
async def simulation_stop() -> RuntimeState:
    runtime.running = False
    await hub.broadcast(
        "analytics",
        {"type": "runtime", "data": runtime.model_dump(mode="json")},
    )
    return runtime


@app.post("/simulation/reset", response_model=RuntimeState)
async def simulation_reset() -> RuntimeState:
    global runtime
    runtime = RuntimeState()
    replay_session.reset()
    portfolio.reset()
    metrics.reset()
    await hub.broadcast("portfolio", {"type": "portfolio", "data": portfolio.snapshot()})
    await hub.broadcast(
        "analytics",
        {"type": "runtime", "data": runtime.model_dump(mode="json")},
    )
    return runtime


def _replay_speed_value(speed: str) -> int:
    if speed == "MAX":
        return 100
    return int(speed.removesuffix("x"))


def _replay_failure(error: RuntimeError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(error))


@app.get("/replay/status")
def replay_status() -> dict[str, object]:
    return {"mode": SystemMode.HISTORICAL_REPLAY, **replay_session.status()}


@app.post("/replay/start")
async def replay_start(request: ReplayStartRequest) -> dict[str, object]:
    if runtime.kill_switch != KillSwitchState.ARMED:
        raise HTTPException(status_code=409, detail="kill switch is not armed")
    status = replay_session.start(request)
    runtime.mode = SystemMode.HISTORICAL_REPLAY
    runtime.running = True
    runtime.replay_speed = _replay_speed_value(request.speed)
    metrics.increment("replay_session_starts")
    await hub.broadcast("analytics", {"type": "replay", "data": status})
    return {"mode": runtime.mode, **status}


@app.post("/replay/pause")
async def replay_pause() -> dict[str, object]:
    try:
        status = replay_session.pause()
    except RuntimeError as error:
        raise _replay_failure(error) from error
    runtime.running = False
    await hub.broadcast("analytics", {"type": "replay", "data": status})
    return {"mode": runtime.mode, **status}


@app.post("/replay/resume")
async def replay_resume() -> dict[str, object]:
    if runtime.kill_switch != KillSwitchState.ARMED:
        raise HTTPException(status_code=409, detail="kill switch is not armed")
    try:
        status = replay_session.resume()
    except RuntimeError as error:
        raise _replay_failure(error) from error
    runtime.mode = SystemMode.HISTORICAL_REPLAY
    runtime.running = True
    await hub.broadcast("analytics", {"type": "replay", "data": status})
    return {"mode": runtime.mode, **status}


@app.post("/replay/step")
async def replay_step() -> dict[str, object]:
    if runtime.kill_switch != KillSwitchState.ARMED:
        raise HTTPException(status_code=409, detail="kill switch is not armed")
    try:
        frame = replay_session.step()
    except RuntimeError as error:
        raise _replay_failure(error) from error

    status = replay_session.status()
    if frame is None:
        runtime.running = False
        await hub.broadcast("analytics", {"type": "replay", "data": status})
        return {"mode": runtime.mode, "frame": None, **status}

    snapshot = frame.snapshot
    mid = (snapshot.bid + snapshot.ask) / 2
    spread = snapshot.ask - snapshot.bid
    market_data = {
        "timestamp": frame.timestamp,
        "market_id": snapshot.market_id,
        "symbol": snapshot.symbol,
        "bid": snapshot.bid,
        "ask": snapshot.ask,
        "mid": mid,
        "spread": spread,
        "market_probability": snapshot.market_probability,
        "volatility": snapshot.volatility,
    }
    orderbook = {
        "timestamp": frame.timestamp,
        "symbol": snapshot.symbol,
        "best_bid": snapshot.bid,
        "best_ask": snapshot.ask,
        "bid_size": snapshot.bid_size,
        "ask_size": snapshot.ask_size,
        "mid_price": mid,
        "spread": spread,
        "imbalance": snapshot.imbalance,
    }
    await hub.broadcast("market-data", {"type": "market-data", "data": market_data})
    await hub.broadcast("orderbook", {"type": "orderbook", "data": orderbook})
    await hub.broadcast("analytics", {"type": "replay", "data": status})
    metrics.increment("replay_session_steps")
    if status["finished"]:
        runtime.running = False
    return {"mode": runtime.mode, "frame": market_data, **status}


@app.post("/replay/restart")
async def replay_restart() -> dict[str, object]:
    if runtime.kill_switch != KillSwitchState.ARMED:
        raise HTTPException(status_code=409, detail="kill switch is not armed")
    try:
        status = replay_session.restart()
    except RuntimeError as error:
        raise _replay_failure(error) from error
    runtime.mode = SystemMode.HISTORICAL_REPLAY
    runtime.running = True
    metrics.increment("replay_session_restarts")
    await hub.broadcast("analytics", {"type": "replay", "data": status})
    return {"mode": runtime.mode, **status}


@app.post("/killswitch/trigger", response_model=RuntimeState)
async def killswitch_trigger() -> RuntimeState:
    runtime.kill_switch = KillSwitchState.TRIGGERED
    runtime.running = False
    if replay_session.active and not replay_session.paused:
        replay_session.pause()
    metrics.increment("kill_switch_triggers")
    await hub.broadcast("risk", {"type": "risk", "data": risk()})
    return runtime


@app.post("/killswitch/reset", response_model=RuntimeState)
async def killswitch_reset() -> RuntimeState:
    if runtime.kill_switch == KillSwitchState.LOCKED:
        runtime.kill_switch = KillSwitchState.RESET_PENDING
    else:
        runtime.kill_switch = KillSwitchState.ARMED
    await hub.broadcast("risk", {"type": "risk", "data": risk()})
    return runtime


@app.websocket("/ws/market-data")
async def ws_market_data(websocket: WebSocket) -> None:
    await hub.serve("market-data", websocket)


@app.websocket("/ws/orderbook")
async def ws_orderbook(websocket: WebSocket) -> None:
    await hub.serve("orderbook", websocket)


@app.websocket("/ws/signals")
async def ws_signals(websocket: WebSocket) -> None:
    await hub.serve("signals", websocket)


@app.websocket("/ws/risk")
async def ws_risk(websocket: WebSocket) -> None:
    await hub.serve("risk", websocket)


@app.websocket("/ws/portfolio")
async def ws_portfolio(websocket: WebSocket) -> None:
    await hub.serve("portfolio", websocket)


@app.websocket("/ws/fills")
async def ws_fills(websocket: WebSocket) -> None:
    await hub.serve("fills", websocket)


@app.websocket("/ws/analytics")
async def ws_analytics(websocket: WebSocket) -> None:
    await hub.serve("analytics", websocket)
