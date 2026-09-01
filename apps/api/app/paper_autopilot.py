from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from math import isfinite
from time import monotonic
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .app_state import runtime
from .live_monitor import live_monitor
from .main import simulate
from .models import KillSwitchState, SimulationRequest, SystemMode
from .risk_state import simulation_execution_allowed

_POLL_SECONDS = 3.0
_RESET_FACTOR = 0.5


class PaperAutopilotConfig(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    symbol: Literal["BTC", "ETH", "SOL"] = "BTC"
    imbalance_trigger: float = Field(default=0.65, ge=0.10, le=0.95)
    cooldown_seconds: float = Field(default=20.0, ge=5.0, le=300.0)
    quantity: float = Field(default=0.001, gt=0, le=1_000)
    max_spread_bps: float = Field(default=20.0, ge=0.01, le=75.0)


class PaperAutopilotService:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._config = PaperAutopilotConfig()
        self._started_at: datetime | None = None
        self._last_cycle_at: datetime | None = None
        self._last_action_at: datetime | None = None
        self._last_action_monotonic = 0.0
        self._last_reason = "STOPPED"
        self._last_signal: dict[str, object] | None = None
        self._last_result: dict[str, object] | None = None
        self._armed_side: str | None = None
        self._cycles = 0
        self._signals = 0
        self._submissions = 0
        self._accepted = 0
        self._rejected = 0
        self._errors = 0
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, config: PaperAutopilotConfig) -> dict[str, object]:
        async with self._lock:
            if not self._paper_runtime_ready():
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "PAPER_TRADING runtime must be running and armed "
                        "before autopilot starts"
                    ),
                )
            self._config = config
            if self.running:
                self._last_reason = "CONFIG_UPDATED"
                return self.status()
            self._started_at = datetime.now(UTC)
            self._last_cycle_at = None
            self._last_action_at = None
            self._last_action_monotonic = 0.0
            self._last_reason = "STARTING"
            self._last_signal = None
            self._last_result = None
            self._armed_side = None
            self._cycles = 0
            self._signals = 0
            self._submissions = 0
            self._accepted = 0
            self._rejected = 0
            self._errors = 0
            self._task = asyncio.create_task(self._run(), name="paper-autopilot")
            return self.status()

    async def stop(self) -> dict[str, object]:
        async with self._lock:
            task = self._task
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            self._task = None
            self._last_reason = "STOPPED"
            self._armed_side = None
            return self.status()

    def status(self) -> dict[str, object]:
        return {
            "mode": runtime.mode,
            "running": self.running,
            "paper_runtime_ready": self._paper_runtime_ready(),
            "live_market_ready": self._live_market_ready(self._config.symbol),
            "kill_switch": runtime.kill_switch,
            "config": self._config.model_dump(mode="json"),
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_cycle_at": self._last_cycle_at.isoformat() if self._last_cycle_at else None,
            "last_action_at": self._last_action_at.isoformat() if self._last_action_at else None,
            "last_reason": self._last_reason,
            "last_signal": self._last_signal,
            "last_result": self._last_result,
            "counters": {
                "cycles": self._cycles,
                "signals": self._signals,
                "submissions": self._submissions,
                "accepted": self._accepted,
                "rejected": self._rejected,
                "errors": self._errors,
            },
            "financial_connectivity": False,
            "real_money_execution": False,
        }

    def _paper_runtime_ready(self) -> bool:
        return (
            runtime.mode == SystemMode.PAPER_TRADING
            and runtime.running
            and runtime.kill_switch == KillSwitchState.ARMED
            and simulation_execution_allowed()
        )

    def _live_market_ready(self, symbol: str) -> bool:
        status = live_monitor.status()
        fresh_symbols = status.get("fresh_symbols")
        return bool(
            status.get("running")
            and status.get("receiving_data")
            and isinstance(fresh_symbols, list)
            and symbol in fresh_symbols
            and status.get("financial_connectivity") is False
            and status.get("real_money_execution") is False
        )

    async def _run(self) -> None:
        try:
            while True:
                await self._cycle()
                await asyncio.sleep(_POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._errors += 1
            self._last_reason = f"WORKER_ERROR:{type(error).__name__}"
            self._task = None

    async def _cycle(self) -> None:
        self._cycles += 1
        self._last_cycle_at = datetime.now(UTC)
        if not self._paper_runtime_ready():
            self._last_reason = "WAITING_FOR_PAPER_RUNTIME"
            return

        config = self._config
        if not self._live_market_ready(config.symbol):
            self._last_reason = "WAITING_FOR_FRESH_LIVE_DATA"
            return

        frame = live_monitor.snapshot(config.symbol)
        analytics = live_monitor.analytics(config.symbol)
        if frame is None or analytics is None:
            self._last_reason = "WAITING_FOR_LIVE_DATA"
            return

        try:
            bid = float(frame["bid"])
            ask = float(frame["ask"])
            bid_size = float(frame["bid_size"])
            ask_size = float(frame["ask_size"])
            imbalance = float(analytics["current_imbalance"])
            volatility = float(analytics["realized_volatility"])
        except (KeyError, TypeError, ValueError):
            self._errors += 1
            self._last_reason = "INVALID_LIVE_PAYLOAD"
            return

        live_values = (bid, ask, bid_size, ask_size, imbalance, volatility)
        if not all(isfinite(value) for value in live_values):
            self._errors += 1
            self._last_reason = "NON_FINITE_LIVE_PAYLOAD"
            return
        if bid <= 0 or ask < bid or bid_size < 0 or ask_size < 0:
            self._errors += 1
            self._last_reason = "INVALID_LIVE_BOOK"
            return

        spread_bps = ((ask - bid) / max(ask, 1e-9)) * 10_000
        self._last_signal = {
            "symbol": config.symbol,
            "imbalance": imbalance,
            "realized_volatility": volatility,
            "spread_bps": spread_bps,
            "observed_at": frame.get("received_at") or frame.get("timestamp"),
        }

        if abs(imbalance) < config.imbalance_trigger * _RESET_FACTOR:
            self._armed_side = None
        if abs(imbalance) < config.imbalance_trigger:
            self._last_reason = "WATCHING_SIGNAL"
            return
        self._signals += 1

        if spread_bps > config.max_spread_bps:
            self._last_reason = "SPREAD_GUARD"
            return

        side = "BUY" if imbalance > 0 else "SELL"
        if self._armed_side == side:
            self._last_reason = "SIGNAL_ALREADY_CONSUMED"
            return

        elapsed = (
            monotonic() - self._last_action_monotonic
            if self._last_action_monotonic
            else float("inf")
        )
        if elapsed < config.cooldown_seconds:
            self._last_reason = "COOLDOWN"
            return

        top_size = ask_size if side == "BUY" else bid_size
        if config.quantity > top_size:
            self._last_reason = "LIQUIDITY_GUARD"
            return

        market_id = f"autopilot-{config.symbol.lower()}-usd"
        limit_price = ask if side == "BUY" else bid
        observed_at = frame.get("received_at") or frame.get("timestamp")
        payload = SimulationRequest.model_validate(
            {
                "order": {
                    "market_id": market_id,
                    "asset": config.symbol,
                    "side": side,
                    "quantity": config.quantity,
                    "limit_price": limit_price,
                },
                "snapshot": {
                    "symbol": config.symbol,
                    "market_id": market_id,
                    "bid": bid,
                    "ask": ask,
                    "bid_size": bid_size,
                    "ask_size": ask_size,
                    "volatility": max(volatility, 0.0),
                    "imbalance": max(-1.0, min(1.0, imbalance)),
                    "observed_at": observed_at,
                },
            }
        )

        self._submissions += 1
        result = await simulate(payload)
        self._last_action_monotonic = monotonic()
        self._last_action_at = datetime.now(UTC)
        self._armed_side = side
        self._last_result = result.model_dump(mode="json")
        if result.accepted:
            self._accepted += 1
            self._last_reason = "SIMULATED_FILL"
        else:
            self._rejected += 1
            self._last_reason = f"RISK_REJECTED:{result.reason}"


paper_autopilot = PaperAutopilotService()


@asynccontextmanager
async def paper_autopilot_lifespan(_: APIRouter) -> AsyncIterator[None]:
    try:
        yield
    finally:
        if paper_autopilot.running:
            await paper_autopilot.stop()


router = APIRouter(
    prefix="/paper/automation",
    tags=["paper-automation"],
    lifespan=paper_autopilot_lifespan,
)


@router.get("/status")
def paper_automation_status() -> dict[str, object]:
    return paper_autopilot.status()


@router.post("/start")
async def paper_automation_start(config: PaperAutopilotConfig) -> dict[str, object]:
    return await paper_autopilot.start(config)


@router.post("/stop")
async def paper_automation_stop() -> dict[str, object]:
    return await paper_autopilot.stop()


__all__ = ["PaperAutopilotConfig", "PaperAutopilotService", "paper_autopilot", "router"]
