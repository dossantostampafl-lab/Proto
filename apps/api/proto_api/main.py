from __future__ import annotations

from fastapi import FastAPI

from . import __version__
from .models import RunMode, SimulationRequest, SimulationResult
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": RunMode.SIMULATION, "version": __version__}


@app.post("/v1/simulate", response_model=SimulationResult)
def simulate(request: SimulationRequest) -> SimulationResult:
    return simulator.simulate(request)
