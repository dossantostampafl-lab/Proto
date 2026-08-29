from __future__ import annotations

from fastapi import FastAPI

from . import __version__
from .models import RunMode, SimulationRequest, SimulationResult
from .portfolio import PaperPortfolio
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


@app.get("/v1/portfolio")
def get_portfolio() -> dict[str, object]:
    return portfolio.snapshot()


@app.post("/v1/portfolio/reset")
def reset_portfolio() -> dict[str, str]:
    portfolio.reset()
    return {"status": "reset", "mode": RunMode.SIMULATION}
