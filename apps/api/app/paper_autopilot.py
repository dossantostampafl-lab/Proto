from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from hashlib import sha256
from math import isfinite
from time import monotonic
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.orchestration import DecisionMemoryEntry, DecisionStage

from .app_state import decision_memory_store, portfolio, runtime
from .live_monitor import live_monitor
from .main import simulate
from .models import KillSwitchState, SimulationRequest, SimulationResult, SystemMode
from .paper_stop_loss import evaluate_stop_loss
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
    stop_loss_fraction: float = Field(gt=0.0, le=0.50)


class PaperAutopilotService:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._config: PaperAutopilotConfig | None = None
        self._started_at: datetime | None = None
        self._last_cycle_at: datetime | None = None
        self._last_action_at: datetime | None = None
        self._last_action_monotonic = 0.0
        self._last_reason = "STOPPED"
        self._last_signal: dict[str, object] | None = None
        self._last_result: dict[str, object] | None = None
        self._last_stop_loss: dict[str, object] | None = None
        self._last_decision_id: str | None = None
        self._decision_memory_failures = 0
        self._armed_side: str | None = None
        self._cycles = 0
        self._signals = 0
        self._submissions = 0
        self._accepted = 0
        self._rejected = 0
        self._stop_loss_exits = 0
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
            previous_symbol = self._config.symbol if self._config is not None else None
            self._config = config
            if self.running:
                if config.symbol != previous_symbol:
                    self._armed_side = None
                    self._last_signal = None
                    self._last_stop_loss = None
                self._last_reason = "CONFIG_UPDATED"
                return self.status()
            self._started_at = datetime.now(UTC)
            self._last_cycle_at = None
            self._last_action_at = None
            self._last_action_monotonic = 0.0
            self._last_reason = "STARTING"
            self._last_signal = None
            self._last_result = None
            self._last_stop_loss = None
            self._last_decision_id = None
            self._decision_memory_failures = 0
            self._armed_side = None
            self._cycles = 0
            self._signals = 0
            self._submissions = 0
            self._accepted = 0
            self._rejected = 0
            self._stop_loss_exits = 0
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
        config = self._config
        return {
            "mode": runtime.mode,
            "running": self.running,
            "paper_runtime_ready": self._paper_runtime_ready(),
            "live_market_ready": (
                self._live_market_ready(config.symbol) if config is not None else False
            ),
            "kill_switch": runtime.kill_switch,
            "config": config.model_dump(mode="json") if config is not None else None,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_cycle_at": self._last_cycle_at.isoformat() if self._last_cycle_at else None,
            "last_action_at": self._last_action_at.isoformat() if self._last_action_at else None,
            "last_reason": self._last_reason,
            "last_signal": self._last_signal,
            "last_result": self._last_result,
            "last_stop_loss": self._last_stop_loss,
            "last_decision_id": self._last_decision_id,
            "decision_memory_enabled": decision_memory_store is not None,
            "counters": {
                "cycles": self._cycles,
                "signals": self._signals,
                "submissions": self._submissions,
                "accepted": self._accepted,
                "rejected": self._rejected,
                "stop_loss_exits": self._stop_loss_exits,
                "decision_memory_failures": self._decision_memory_failures,
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
        symbol_health = status.get("symbol_health")
        health = symbol_health.get(symbol) if isinstance(symbol_health, Mapping) else None
        source_health = status.get("feed_health")
        source_connected = (
            source_health.get("connected") if isinstance(source_health, Mapping) else None
        )
        return bool(
            status.get("running")
            and status.get("receiving_data")
            and status.get("source_message_fresh") is True
            and source_connected is True
            and isinstance(health, Mapping)
            and health.get("fresh") is True
            and health.get("receipt_fresh") is True
            and health.get("current_connection") is True
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

    def _position_for_symbol(self, symbol: str) -> Mapping[str, object] | None:
        snapshot = portfolio.snapshot()
        positions = snapshot.get("positions")
        if not isinstance(positions, list):
            return None
        for position in positions:
            if isinstance(position, Mapping) and position.get("asset") == symbol:
                return position
        return None

    async def _record_decision(
        self,
        *,
        request: SimulationRequest,
        result: SimulationResult,
        reason: str,
    ) -> None:
        if decision_memory_store is None:
            return
        input_hash = sha256(
            request.model_dump_json(exclude={"order": {"id", "created_at"}}).encode("utf-8")
        ).hexdigest()
        stage = DecisionStage.PAPER_EXECUTED if result.accepted else DecisionStage.RISK_REJECTED
        entry = DecisionMemoryEntry(
            decision_id=request.order.id,
            instrument_id=f"CRYPTO:{request.order.asset.value}",
            observed_at=request.snapshot.observed_at,
            recorded_at=datetime.now(UTC),
            stage=stage,
            input_hash=input_hash,
            risk_decision=result.reason,
            proposed_action=request.order.side.value,
            actual_action=request.order.side.value if result.accepted else None,
            explanation=reason,
            provenance={
                "decision_source": "PAPER_AUTOPILOT",
                "market_data_source": "PUBLIC_READ_ONLY",
                "market_id": request.order.market_id,
                "system_mode": SystemMode.PAPER_TRADING.value,
            },
        )
        try:
            await decision_memory_store.record(entry)
        except Exception:
            self._decision_memory_failures += 1
            self._errors += 1
            return
        self._last_decision_id = str(entry.decision_id)

    async def _submit(
        self,
        *,
        config: PaperAutopilotConfig,
        side: str,
        quantity: float,
        bid: float,
        ask: float,
        bid_size: float,
        ask_size: float,
        volatility: float,
        imbalance: float,
        observed_at: object,
        reason: str,
    ) -> bool:
        if not self._live_market_ready(config.symbol):
            self._last_reason = "LIVE_DATA_BECAME_STALE"
            return False
        market_id = f"autopilot-{config.symbol.lower()}-usd"
        payload = SimulationRequest.model_validate(
            {
                "order": {
                    "market_id": market_id,
                    "asset": config.symbol,
                    "side": side,
                    "quantity": quantity,
                    "limit_price": ask if side == "BUY" else bid,
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
        await self._record_decision(request=payload, result=result, reason=reason)
        self._last_action_monotonic = monotonic()
        self._last_action_at = datetime.now(UTC)
        self._last_result = result.model_dump(mode="json")
        if result.accepted:
            self._accepted += 1
            self._last_reason = reason
            return True
        self._rejected += 1
        self._last_reason = f"RISK_REJECTED:{result.reason}"
        return False

    async def _cycle(self) -> None:
        self._cycles += 1
        self._last_cycle_at = datetime.now(UTC)
        if not self._paper_runtime_ready():
            self._last_reason = "WAITING_FOR_PAPER_RUNTIME"
            return
        config = self._config
        if config is None:
            self._last_reason = "CONFIG_REQUIRED"
            return
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
        values = (bid, ask, bid_size, ask_size, imbalance, volatility)
        if not all(isfinite(value) for value in values):
            self._errors += 1
            self._last_reason = "NON_FINITE_LIVE_PAYLOAD"
            return
        if bid <= 0 or ask < bid or bid_size < 0 or ask_size < 0:
            self._errors += 1
            self._last_reason = "INVALID_LIVE_BOOK"
            return

        observed_at = frame.get("received_at") or frame.get("timestamp")
        spread_bps = ((ask - bid) / max(ask, 1e-9)) * 10_000
        self._last_signal = {
            "symbol": config.symbol,
            "imbalance": imbalance,
            "realized_volatility": volatility,
            "spread_bps": spread_bps,
            "observed_at": observed_at,
        }

        position = self._position_for_symbol(config.symbol)
        if position is not None:
            try:
                position_quantity = float(position.get("quantity", 0.0))
                average_price = float(position.get("average_price", 0.0))
                stop = evaluate_stop_loss(
                    position_quantity=position_quantity,
                    average_price=average_price,
                    bid=bid,
                    ask=ask,
                    stop_loss_fraction=config.stop_loss_fraction,
                )
            except (TypeError, ValueError):
                self._errors += 1
                self._last_reason = "INVALID_POSITION_FOR_STOP_LOSS"
                return
            self._last_stop_loss = {
                "triggered": stop.triggered,
                "threshold_price": stop.threshold_price,
                "reason": stop.reason,
                "position_quantity": position_quantity,
            }
            if stop.triggered and stop.side is not None:
                top_size = ask_size if stop.side == "BUY" else bid_size
                if stop.quantity > top_size:
                    self._last_reason = "STOP_LOSS_LIQUIDITY_GUARD"
                    return
                closed = await self._submit(
                    config=config,
                    side=stop.side,
                    quantity=stop.quantity,
                    bid=bid,
                    ask=ask,
                    bid_size=bid_size,
                    ask_size=ask_size,
                    volatility=volatility,
                    imbalance=imbalance,
                    observed_at=observed_at,
                    reason="STOP_LOSS_SIMULATED_FILL",
                )
                if closed:
                    self._stop_loss_exits += 1
                    self._armed_side = None
                return

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
        submissions_before = self._submissions
        await self._submit(
            config=config,
            side=side,
            quantity=config.quantity,
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            volatility=volatility,
            imbalance=imbalance,
            observed_at=observed_at,
            reason="SIMULATED_FILL",
        )
        if self._submissions > submissions_before:
            self._armed_side = side


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
