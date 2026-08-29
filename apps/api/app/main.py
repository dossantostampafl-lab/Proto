from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from services.events.journal import (
    HashChainJournal,
    JournalRecord,
    ResearchEvent,
)
from services.market_data.replay import (
    MarketDataEvent,
    ReplayBatch,
    ReplaySummary,
    select_replay_window,
    summarize_replay,
)
from services.quant.calibration import (
    CalibrationBatch,
    CalibrationMetrics,
    score_calibration,
)
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
from .observability import RuntimeMetrics, access_log
from .persistence import AsyncSqlFillJournal, build_engine, database_ready, init_database
from .portfolio import PaperPortfolio
from .settings import settings
from .simulation import PaperSimulator

logger = logging.getLogger("proto.api")
metrics = RuntimeMetrics()
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

runtime = RuntimeState()
simulator = PaperSimulator()
portfolio = PaperPortfolio()
research_journal = HashChainJournal()


@app.middleware("http")
async def observe_request(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    started = perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        latency_ms = (perf_counter() - started) * 1_000
        metrics.record(path=request.url.path, status_code=500, latency_ms=latency_ms)
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
    metrics.record(
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
def runtime_metrics() -> dict[str, object]:
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
    return estimate_probability(
        market_probability=snapshot.market_probability,
        volatility=snapshot.volatility,
        imbalance=snapshot.imbalance,
    )


@app.post("/calibration/score", response_model=CalibrationMetrics)
def calibration_score(batch: CalibrationBatch) -> CalibrationMetrics:
    return score_calibration(batch)


@app.post("/edge/evaluate", response_model=EdgeBreakdown)
def edge(snapshot: MarketSnapshot) -> EdgeBreakdown:
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


@app.post("/research/replay/summary", response_model=ReplaySummary)
def replay_summary(batch: ReplayBatch) -> ReplaySummary:
    return summarize_replay(batch)


@app.post("/research/replay/window", response_model=list[MarketDataEvent])
def replay_window(
    batch: ReplayBatch,
    after_sequence: int = Query(default=-1, ge=-1),
    limit: int = Query(default=100, ge=1, le=1_000),
) -> list[MarketDataEvent]:
    return select_replay_window(
        batch,
        after_sequence=after_sequence,
        limit=limit,
    )


@app.post("/research/events", response_model=JournalRecord)
def append_research_event(event: ResearchEvent) -> JournalRecord:
    return research_journal.append(event)


@app.get("/research/events", response_model=list[JournalRecord])
def list_research_events(
    limit: int = Query(default=100, ge=1, le=1_000),
) -> list[JournalRecord]:
    return research_journal.list(limit)


@app.get("/research/events/verify")
def verify_research_events() -> dict[str, object]:
    records = research_journal.list(research_journal.max_records)
    return {
        "valid": research_journal.verify(),
        "count": len(records),
        "mode": SystemMode.SIMULATION,
    }


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
    if not runtime.running or runtime.kill_switch != KillSwitchState.ARMED:
        return SimulationResult(accepted=False, reason="simulation halted")

    result = simulator.simulate(request)
    if result.accepted and result.fill is not None:
        portfolio.apply_fill(request.order, result.fill)
        if persistent_journal is not None:
            await persistent_journal.append(request.order, result.fill)
    return result


@app.get("/v1/portfolio")
def get_portfolio() -> dict[str, object]:
    return portfolio.snapshot()


@app.post("/v1/portfolio/mark")
def mark_portfolio(request: PortfolioMarkRequest) -> dict[str, object]:
    marks = {mark.asset: mark.price for mark in request.marks}
    return portfolio.snapshot(marks)


@app.get("/v1/fills")
async def get_fills(limit: int = Query(default=100, ge=1, le=1_000)) -> dict[str, object]:
    entries = (
        await persistent_journal.list(limit)
        if persistent_journal is not None
        else portfolio.journal(limit)
    )
    return {"mode": SystemMode.SIMULATION, "count": len(entries), "fills": entries}


@app.post("/simulation/start", response_model=RuntimeState)
def simulation_start() -> RuntimeState:
    if runtime.kill_switch != KillSwitchState.ARMED:
        runtime.running = False
        return runtime
    runtime.mode = SystemMode.SIMULATION
    runtime.running = True
    return runtime


@app.post("/simulation/stop", response_model=RuntimeState)
def simulation_stop() -> RuntimeState:
    runtime.running = False
    return runtime


@app.post("/simulation/reset", response_model=RuntimeState)
def simulation_reset() -> RuntimeState:
    global runtime
    runtime = RuntimeState()
    portfolio.reset()
    return runtime


@app.post("/killswitch/trigger", response_model=RuntimeState)
def killswitch_trigger() -> RuntimeState:
    runtime.kill_switch = KillSwitchState.TRIGGERED
    runtime.running = False
    return runtime


@app.post("/killswitch/reset", response_model=RuntimeState)
def killswitch_reset() -> RuntimeState:
    if runtime.kill_switch == KillSwitchState.LOCKED:
        runtime.kill_switch = KillSwitchState.RESET_PENDING
    else:
        runtime.kill_switch = KillSwitchState.ARMED
    return runtime
