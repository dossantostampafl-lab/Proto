from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
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
from .observability import LatencyTimer
from .persistence import AsyncSqlFillJournal, build_engine, init_database
from .portfolio import PaperPortfolio
from .research import metrics
from .research import router as research_router
from .settings import settings
from .simulation import PaperSimulator

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
    allow_headers=["Content-Type"],
)
app.include_router(research_router)

runtime = RuntimeState()
simulator = PaperSimulator()
portfolio = PaperPortfolio()


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "mode": runtime.mode,
        "version": __version__,
        "persistence_enabled": settings.persistence_enabled,
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
    else:
        metrics.increment("simulation_rejected")
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
    metrics.reset()
    return runtime


@app.post("/killswitch/trigger", response_model=RuntimeState)
def killswitch_trigger() -> RuntimeState:
    runtime.kill_switch = KillSwitchState.TRIGGERED
    runtime.running = False
    metrics.increment("kill_switch_triggers")
    return runtime


@app.post("/killswitch/reset", response_model=RuntimeState)
def killswitch_reset() -> RuntimeState:
    if runtime.kill_switch == KillSwitchState.LOCKED:
        runtime.kill_switch = KillSwitchState.RESET_PENDING
    else:
        runtime.kill_switch = KillSwitchState.ARMED
    return runtime
