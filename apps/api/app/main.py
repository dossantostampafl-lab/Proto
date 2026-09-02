from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from services.quant.core import ProbabilityEstimate, estimate_probability

from . import __version__
from .app_state import (
    persistence_engine,
    persistent_journal,
    portfolio,
    replay_session,
    reset_runtime_state,
    runtime,
    simulator,
)
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
from .persistence import database_ready, init_database
from .reconciliation_service import reconciliation_status as build_reconciliation_status
from .replay import (
    ReplaySeekRequest,
    ReplaySpeedRequest,
    ReplayStartRequest,
)
from .research import metrics
from .research import router as research_router
from .risk_state import risk_snapshot
from .settings import settings
from .simulation_policy import authoritative_simulation_request
from .websockets import hub

logger = logging.getLogger("proto.api")


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


@app.post("/probability/estimate", response_model=ProbabilityEstimate)
def probability(snapshot: MarketSnapshot) -> ProbabilityEstimate:
    metrics.increment("probability_requests")
    return estimate_probability(
        market_probability=snapshot.market_probability,
        volatility=snapshot.volatility,
        imbalance=snapshot.imbalance,
    )


@app.get("/risk")
def risk() -> dict[str, object]:
    return risk_snapshot()


@app.post("/v1/simulate", response_model=SimulationResult)
async def simulate(request: SimulationRequest) -> SimulationResult:
    metrics.increment("simulation_requests")
    if not runtime.running or runtime.kill_switch != KillSwitchState.ARMED:
        metrics.increment("simulation_rejected")
        return SimulationResult(accepted=False, reason="simulation halted")

    effective_request = authoritative_simulation_request(
        request,
        portfolio.snapshot(),
        max_order_notional=settings.simulation_max_order_notional,
        max_position_notional=settings.simulation_max_position_notional,
        max_slippage_bps=settings.simulation_max_slippage_bps,
    )
    with LatencyTimer(metrics):
        result = simulator.simulate(effective_request)

    if result.accepted and result.fill is not None:
        if persistent_journal is not None:
            try:
                inserted = await persistent_journal.append(effective_request.order, result.fill)
            except Exception:
                metrics.increment("simulation_persistence_failures")
                metrics.increment("simulation_rejected")
                logger.exception("simulation persistence commit failed")
                return SimulationResult(
                    accepted=False,
                    reason="simulation persistence unavailable",
                )
            if not inserted:
                metrics.increment("simulation_duplicate_orders")
                metrics.increment("simulation_rejected")
                return SimulationResult(
                    accepted=False,
                    reason="duplicate simulated order",
                )

        applied = portfolio.apply_fill(effective_request.order, result.fill)
        if not applied:
            metrics.increment("simulation_duplicate_orders")
            metrics.increment("simulation_rejected")
            return SimulationResult(
                accepted=False,
                reason="duplicate simulated order",
            )

        metrics.increment("simulation_accepted")
        fill_event = {
            **result.fill.model_dump(mode="json"),
            "market_id": effective_request.order.market_id,
            "asset": effective_request.order.asset.value,
            "side": effective_request.order.side.value,
        }
        await hub.broadcast(
            "fills",
            {"type": "fill", "data": fill_event},
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


@app.get("/v1/reconciliation")
async def reconciliation_status() -> dict[str, object]:
    return await build_reconciliation_status()


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
    if persistent_journal is not None:
        try:
            await persistent_journal.start_new_session()
        except Exception as error:
            metrics.increment("simulation_session_reset_failures")
            logger.exception("simulation session rotation failed")
            raise HTTPException(
                status_code=503,
                detail="simulation persistence unavailable",
            ) from error

    reset_runtime_state()
    replay_session.reset()
    portfolio.reset()
    metrics.reset()
    metrics.increment("simulation_session_resets")
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


async def _rotate_replay_persistence() -> None:
    if persistent_journal is None:
        return
    try:
        await persistent_journal.start_new_session()
    except Exception as error:
        metrics.increment("replay_persistence_rotation_failures")
        logger.exception("replay persistence session rotation failed")
        raise HTTPException(
            status_code=503,
            detail="replay persistence unavailable",
        ) from error


@app.get("/replay/status")
def replay_status() -> dict[str, object]:
    return {"mode": SystemMode.HISTORICAL_REPLAY, **replay_session.status()}


@app.post("/replay/start")
async def replay_start(request: ReplayStartRequest) -> dict[str, object]:
    if runtime.kill_switch != KillSwitchState.ARMED:
        raise HTTPException(status_code=409, detail="kill switch is not armed")

    ReplaySessionProbe = type(replay_session)
    probe = ReplaySessionProbe()
    try:
        probe.start(request)
    except (RuntimeError, ValueError) as error:
        raise _replay_failure(RuntimeError(str(error))) from error

    await _rotate_replay_persistence()
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
    if not replay_session.active:
        raise _replay_failure(RuntimeError("replay session has not been started"))

    await _rotate_replay_persistence()
    status = replay_session.restart()
    runtime.mode = SystemMode.HISTORICAL_REPLAY
    runtime.running = True
    metrics.increment("replay_session_restarts")
    await hub.broadcast("analytics", {"type": "replay", "data": status})
    return {"mode": runtime.mode, **status}


@app.post("/replay/seek")
async def replay_seek(request: ReplaySeekRequest) -> dict[str, object]:
    replay_state = replay_session.status()
    if not replay_state["active"]:
        raise _replay_failure(RuntimeError("replay session has not been started"))
    if request.cursor > int(replay_state["total_frames"]):
        raise _replay_failure(RuntimeError("replay cursor exceeds total frames"))

    await _rotate_replay_persistence()
    status = replay_session.seek(request.cursor)
    runtime.running = False
    await hub.broadcast("analytics", {"type": "replay", "data": status})
    return {"mode": runtime.mode, **status}


@app.post("/replay/speed")
async def replay_speed(request: ReplaySpeedRequest) -> dict[str, object]:
    try:
        status = replay_session.set_speed(request.speed)
    except RuntimeError as error:
        raise _replay_failure(error) from error
    runtime.replay_speed = _replay_speed_value(request.speed)
    await hub.broadcast("analytics", {"type": "replay", "data": status})
    return {"mode": runtime.mode, **status}


@app.post("/replay/reset")
async def replay_reset() -> dict[str, object]:
    replay_session.reset()
    runtime.mode = SystemMode.HISTORICAL_REPLAY
    runtime.running = False
    runtime.replay_speed = 1
    status = replay_session.status()
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
