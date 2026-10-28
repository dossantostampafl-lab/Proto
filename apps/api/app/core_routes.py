"""Research, simulation, replay and kill-switch routes shared by the Proto API."""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import APIRouter, HTTPException, Query, Response, WebSocket

from services.quant.core import ProbabilityEstimate, compute_edge, estimate_probability

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
from .observability import LatencyTimer
from .persistence import database_ready
from .reconciliation_service import reconciliation_status as build_reconciliation_status
from .replay import (
    ReplaySeekRequest,
    ReplaySpeedRequest,
    ReplayStartRequest,
)
from .research import metrics
from .risk_state import risk_snapshot
from .runtime_control import broadcast_runtime, start_runtime, stop_runtime
from .settings import settings
from .simulation_policy import authoritative_simulation_request
from .websockets import hub

logger = logging.getLogger("proto.api")

router = APIRouter()
_simulation_lock = asyncio.Lock()


@router.get("/health")
def health() -> dict[str, object]:
    from . import control_state
    from .floor_surface import data_mode

    control = control_state.current().snapshot()
    return {
        "status": "ok",
        # The simulation runtime's mode, not the system's: see `system_mode`.
        "mode": runtime.mode,
        "system_mode": settings.system_mode,
        # Where account balances come from: the paper ledger or the testnet executor.
        "account_source": data_mode(),
        "version": __version__,
        "git_commit_sha": os.getenv("PROTO_GIT_COMMIT_SHA", "").strip() or None,
        # General simulation persistence only; controls and checkpoints are `control_plane`.
        "persistence_enabled": settings.persistence_enabled,
        "control_plane": {"status": control["status"], "durable": control["durable"]},
        "financial_connectivity": False,
        "real_money_execution": False,
    }


@router.get("/ready")
async def ready(response: Response) -> dict[str, object]:
    from . import control_state

    control = control_state.current()
    control_ok = await control.ensure()
    if control_ok and control.engine is not None and not await database_ready(control.engine):
        control.mark_unavailable(ConnectionError("control store did not answer SELECT 1"))
        control_ok = False
    if persistence_engine is None:
        database = "disabled"
        ready_state = control_ok
    else:
        database_ok = await database_ready(persistence_engine)
        database = "ready" if database_ok else "unavailable"
        ready_state = database_ok and control_ok
    if not ready_state:
        response.status_code = 503
    return {
        "status": "ready" if ready_state else "not_ready",
        "database": database,
        "mode": runtime.mode,
        # Truthful label: whether kill switches, lockdown, checkpoints, permits, promotion and
        # the journal survive a restart, or live only in this process.
        "control_plane": control.snapshot(),
    }


@router.get("/metrics")
def system_metrics() -> dict[str, object]:
    return {
        "mode": runtime.mode,
        "real_money_execution": False,
        **metrics.snapshot(),
    }


@router.get("/system/status", response_model=RuntimeState)
def system_status() -> RuntimeState:
    return runtime


@router.post("/probability/estimate", response_model=ProbabilityEstimate)
def probability(snapshot: MarketSnapshot) -> ProbabilityEstimate:
    metrics.increment("probability_requests")
    return estimate_probability(
        market_probability=snapshot.market_probability,
        volatility=snapshot.volatility,
        imbalance=snapshot.imbalance,
    )


@router.post("/edge/evaluate")
def evaluate_edge_with_explicit_costs(
    snapshot: MarketSnapshot,
    fees: float | None = Query(default=None, ge=0.0),
    slippage: float | None = Query(default=None, ge=0.0),
    hedge_cost: float | None = Query(default=None, ge=0.0),
    latency_penalty: float | None = Query(default=None, ge=0.0),
) -> dict[str, object]:
    metrics.increment("edge_requests")
    provided = {
        "fees": fees,
        "slippage": slippage,
        "hedge_cost": hedge_cost,
        "latency_penalty": latency_penalty,
    }
    unavailable = [name for name, value in provided.items() if value is None]
    if unavailable:
        raise HTTPException(
            status_code=422,
            detail={
                "status": "COSTS_UNAVAILABLE",
                "cost_policy": "EXPLICIT_EXECUTION_COSTS_REQUIRED",
                "unavailable_costs": unavailable,
            },
        )

    estimate = probability(snapshot)
    spread_cost = max(snapshot.ask - snapshot.bid, 0.0) / max(
        snapshot.ask + snapshot.bid,
        1e-9,
    )
    uncertainty_penalty = estimate.uncertainty * 0.02
    result = compute_edge(
        model_probability=estimate.probability,
        market_probability=snapshot.market_probability,
        fees=float(fees),
        slippage=float(slippage),
        spread_cost=spread_cost,
        hedge_cost=float(hedge_cost),
        uncertainty_penalty=uncertainty_penalty,
        latency_penalty=float(latency_penalty),
        minimum_edge=settings.minimum_net_edge,
    )
    return {
        **result.model_dump(),
        "cost_policy": "EXPLICIT_EXECUTION_COSTS",
        "costs_complete": True,
        "known_costs": {
            "fees": fees,
            "slippage": slippage,
            "spread_cost": spread_cost,
            "hedge_cost": hedge_cost,
            "latency_penalty": latency_penalty,
            "uncertainty_penalty": uncertainty_penalty,
        },
    }


@router.get("/risk")
def risk() -> dict[str, object]:
    return risk_snapshot()


@router.post("/v1/simulate", response_model=SimulationResult)
async def simulate(request: SimulationRequest) -> SimulationResult:
    # Risk check, journal append and portfolio update form one critical section, so two
    # concurrent orders can never both pass against the same pre-trade portfolio snapshot.
    async with _simulation_lock:
        return await _simulate_locked(request)


async def _simulate_locked(request: SimulationRequest) -> SimulationResult:
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


@router.get("/v1/portfolio")
def get_portfolio() -> dict[str, object]:
    return portfolio.snapshot()


@router.post("/v1/portfolio/mark")
async def mark_portfolio(request: PortfolioMarkRequest) -> dict[str, object]:
    marks = {mark.asset: mark.price for mark in request.marks}
    snapshot = portfolio.snapshot(marks)
    await hub.broadcast("portfolio", {"type": "portfolio", "data": snapshot})
    return snapshot


@router.get("/v1/fills")
async def get_fills(limit: int = Query(default=100, ge=1, le=1_000)) -> dict[str, object]:
    entries = (
        await persistent_journal.list(limit)
        if persistent_journal is not None
        else portfolio.journal(limit)
    )
    return {"mode": SystemMode.SIMULATION, "count": len(entries), "fills": entries}


@router.get("/v1/reconciliation")
async def reconciliation_status() -> dict[str, object]:
    return await build_reconciliation_status()


@router.post("/simulation/start", response_model=RuntimeState)
async def simulation_start() -> RuntimeState:
    return await start_runtime(SystemMode.SIMULATION)


@router.post("/simulation/stop", response_model=RuntimeState)
async def simulation_stop() -> RuntimeState:
    return await stop_runtime()


@router.post("/simulation/reset", response_model=RuntimeState)
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
    await broadcast_runtime()
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


@router.get("/replay/status")
def replay_status() -> dict[str, object]:
    return {"mode": SystemMode.HISTORICAL_REPLAY, **replay_session.status()}


@router.post("/replay/start")
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


@router.post("/replay/pause")
async def replay_pause() -> dict[str, object]:
    try:
        status = replay_session.pause()
    except RuntimeError as error:
        raise _replay_failure(error) from error
    runtime.running = False
    await hub.broadcast("analytics", {"type": "replay", "data": status})
    return {"mode": runtime.mode, **status}


@router.post("/replay/resume")
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


@router.post("/replay/step")
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


@router.post("/replay/restart")
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


@router.post("/replay/seek")
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


@router.post("/replay/speed")
async def replay_speed(request: ReplaySpeedRequest) -> dict[str, object]:
    try:
        status = replay_session.set_speed(request.speed)
    except RuntimeError as error:
        raise _replay_failure(error) from error
    runtime.replay_speed = _replay_speed_value(request.speed)
    await hub.broadcast("analytics", {"type": "replay", "data": status})
    return {"mode": runtime.mode, **status}


@router.post("/replay/reset")
async def replay_reset() -> dict[str, object]:
    replay_session.reset()
    runtime.mode = SystemMode.HISTORICAL_REPLAY
    runtime.running = False
    runtime.replay_speed = 1
    status = replay_session.status()
    await hub.broadcast("analytics", {"type": "replay", "data": status})
    return {"mode": runtime.mode, **status}


@router.post("/killswitch/trigger", response_model=RuntimeState)
async def killswitch_trigger() -> RuntimeState:
    runtime.kill_switch = KillSwitchState.TRIGGERED
    runtime.running = False
    if replay_session.active and not replay_session.paused:
        replay_session.pause()
    metrics.increment("kill_switch_triggers")
    await hub.broadcast("risk", {"type": "risk", "data": risk()})
    return runtime


@router.post("/killswitch/reset", response_model=RuntimeState)
async def killswitch_reset() -> RuntimeState:
    runtime.kill_switch = KillSwitchState.ARMED
    await hub.broadcast("risk", {"type": "risk", "data": risk()})
    return runtime


@router.websocket("/ws/signals")
async def ws_signals(websocket: WebSocket) -> None:
    await hub.serve("signals", websocket)


@router.websocket("/ws/risk")
async def ws_risk(websocket: WebSocket) -> None:
    await hub.serve("risk", websocket)


@router.websocket("/ws/portfolio")
async def ws_portfolio(websocket: WebSocket) -> None:
    await hub.serve("portfolio", websocket)


@router.websocket("/ws/fills")
async def ws_fills(websocket: WebSocket) -> None:
    await hub.serve("fills", websocket)


@router.websocket("/ws/analytics")
async def ws_analytics(websocket: WebSocket) -> None:
    await hub.serve("analytics", websocket)
