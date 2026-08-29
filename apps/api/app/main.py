from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from services.quant.core import EdgeBreakdown, ProbabilityEstimate, compute_edge, estimate_probability

app = FastAPI(title="Prediction Market Quant Engine", version="0.1.0")


class SystemMode(StrEnum):
    SIMULATION = "SIMULATION"
    PAPER_TRADING = "PAPER_TRADING"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"


class KillSwitchState(StrEnum):
    ARMED = "ARMED"
    TRIGGERED = "TRIGGERED"
    LOCKED = "LOCKED"
    RESET_PENDING = "RESET_PENDING"


class RuntimeState(BaseModel):
    mode: SystemMode = SystemMode.SIMULATION
    running: bool = True
    kill_switch: KillSwitchState = KillSwitchState.ARMED
    replay_speed: int = 1
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MarketSnapshot(BaseModel):
    symbol: Literal["BTC", "ETH", "SOL"]
    bid: float
    ask: float
    bid_size: float = 1.0
    ask_size: float = 1.0
    volatility: float = 0.2
    imbalance: float = 0.0
    market_probability: float = Field(ge=0.0, le=1.0)


runtime = RuntimeState()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": runtime.mode}


@app.get("/system/status")
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
    )


@app.get("/risk")
def risk() -> dict[str, object]:
    return {
        "kill_switch": runtime.kill_switch,
        "execution_allowed": runtime.running and runtime.kill_switch == KillSwitchState.ARMED,
        "real_money_execution": False,
        "minimum_net_edge": 0.01,
        "minimum_confidence": 0.55,
    }


@app.post("/simulation/start")
def simulation_start() -> RuntimeState:
    if runtime.kill_switch != KillSwitchState.ARMED:
        raise HTTPException(status_code=409, detail="kill switch is not armed")
    runtime.mode = SystemMode.SIMULATION
    runtime.running = True
    return runtime


@app.post("/simulation/stop")
def simulation_stop() -> RuntimeState:
    runtime.running = False
    return runtime


@app.post("/simulation/reset")
def simulation_reset() -> RuntimeState:
    global runtime
    runtime = RuntimeState()
    return runtime


@app.post("/killswitch/trigger")
def killswitch_trigger() -> RuntimeState:
    runtime.kill_switch = KillSwitchState.TRIGGERED
    runtime.running = False
    return runtime


@app.post("/killswitch/reset")
def killswitch_reset() -> RuntimeState:
    if runtime.kill_switch == KillSwitchState.LOCKED:
        runtime.kill_switch = KillSwitchState.RESET_PENDING
    else:
        runtime.kill_switch = KillSwitchState.ARMED
    return runtime
