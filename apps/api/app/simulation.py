from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .models import Fill, Side, SimulationRequest, SimulationResult


@dataclass(frozen=True)
class SimulationConfig:
    fee_bps: float = 2.0
    base_slippage_bps: float = 3.0
    max_snapshot_age_seconds: float = 10.0
    max_future_skew_seconds: float = 1.0


class RiskEngine:
    def validate(
        self,
        request: SimulationRequest,
        estimated_slippage_bps: float,
    ) -> tuple[bool, str]:
        order_notional = request.order.quantity * request.order.limit_price
        signed_order_quantity = (
            request.order.quantity if request.order.side == Side.BUY else -request.order.quantity
        )
        projected_quantity = request.current_position_quantity + signed_order_quantity
        projected_position_notional = abs(projected_quantity) * request.order.limit_price

        if request.order.market_id != request.snapshot.market_id:
            return False, "market mismatch"
        if order_notional > request.limits.max_order_notional:
            return False, "max order notional exceeded"
        if projected_position_notional > request.limits.max_position_notional:
            return False, "max position notional exceeded"
        if estimated_slippage_bps > request.limits.max_slippage_bps:
            return False, "max slippage exceeded"
        return True, "accepted"


class PaperSimulator:
    def __init__(self, config: SimulationConfig | None = None) -> None:
        self.config = config or SimulationConfig()
        self.risk = RiskEngine()

    def _validate_snapshot_time(self, request: SimulationRequest) -> tuple[bool, str]:
        observed_at = request.snapshot.observed_at
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            return False, "snapshot timestamp must be timezone-aware"
        now = datetime.now(UTC)
        age_seconds = (now - observed_at).total_seconds()
        if age_seconds > self.config.max_snapshot_age_seconds:
            return False, "stale market snapshot"
        if age_seconds < -self.config.max_future_skew_seconds:
            return False, "market snapshot timestamp is in the future"
        return True, "accepted"

    def simulate(self, request: SimulationRequest) -> SimulationResult:
        snapshot_time_valid, snapshot_time_reason = self._validate_snapshot_time(request)
        if not snapshot_time_valid:
            return SimulationResult(accepted=False, reason=snapshot_time_reason)

        order = request.order
        snapshot = request.snapshot
        executable_price = snapshot.ask if order.side == Side.BUY else snapshot.bid

        if order.side == Side.BUY and order.limit_price < executable_price:
            return SimulationResult(accepted=False, reason="buy limit below ask")
        if order.side == Side.SELL and order.limit_price > executable_price:
            return SimulationResult(accepted=False, reason="sell limit above bid")

        spread_bps = ((snapshot.ask - snapshot.bid) / executable_price) * 10_000
        slippage_bps = self.config.base_slippage_bps + max(spread_bps * 0.10, 0)
        accepted, reason = self.risk.validate(request, slippage_bps)
        if not accepted:
            return SimulationResult(accepted=False, reason=reason)

        direction = 1 if order.side == Side.BUY else -1
        fill_price = executable_price * (1 + direction * slippage_bps / 10_000)
        notional = order.quantity * fill_price
        fee = notional * self.config.fee_bps / 10_000
        fill = Fill(
            order_id=order.id,
            market_id=order.market_id,
            asset=order.asset,
            side=order.side,
            filled_quantity=order.quantity,
            fill_price=round(fill_price, 10),
            fee=round(fee, 10),
            slippage_bps=round(slippage_bps, 6),
        )
        return SimulationResult(accepted=True, reason="simulated fill", fill=fill)