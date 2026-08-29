from __future__ import annotations

from fastapi import FastAPI, Query

from . import __version__
from .models import (
    EdgeEstimate,
    EdgeRequest,
    PortfolioMarkRequest,
    RunMode,
    SimulationRequest,
    SimulationResult,
)
from .portfolio import PaperPortfolio
from .quant import estimate_binary_edge
from .simulation import PaperSimulator

app = FastAPI(
    title="Proto Prediction Market Quant Engine",
    version=__version__,
    description=(
        "Research, simulation and paper-trading API. "
        "Real execution is not part of the MVP."
    ),
)

simulator = PaperSimulator()
portfolio = PaperPortfolio()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": RunMode.SIMULATION, "version": __version__}


@app.post("/v1/simulate", response_model=SimulationResult)
def simulate(request: SimulationRequest) -> SimulationResult:
    result = simulator.simulate(request)
    if result.accepted and result.fill is not None:
        portfolio.apply_fill(request.order, result.fill)
    return result


@app.post("/v1/edge", response_model=EdgeEstimate)
def edge(request: EdgeRequest) -> EdgeEstimate:
    return estimate_binary_edge(request)


@app.get("/v1/portfolio")
def get_portfolio() -> dict[str, object]:
    return portfolio.snapshot()


@app.post("/v1/portfolio/mark")
def mark_portfolio(request: PortfolioMarkRequest) -> dict[str, object]:
    marks = {mark.asset: mark.price for mark in request.marks}
    return portfolio.snapshot(marks)


@app.get("/v1/fills")
def get_fills(limit: int = Query(default=100, ge=1, le=1_000)) -> dict[str, object]:
    entries = portfolio.journal(limit)
    return {"mode": RunMode.SIMULATION, "count": len(entries), "fills": entries}


@app.post("/v1/portfolio/reset")
def reset_portfolio() -> dict[str, str]:
    portfolio.reset()
    return {"status": "reset", "mode": RunMode.SIMULATION}
