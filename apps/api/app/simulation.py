from __future__ import annotations

from dataclasses import dataclass

from .models import Fill, Side, SimulationRequest, SimulationResult


@dataclass(frozen=True)
class SimulationConfig:
    fee_bps: float = 2.0
    base_slippage_bps: float = 3.0


class RiskEngine:
    def validate(
        self,
        request: SimulationRequest,
        estimated_slippage_bps: float,
    ) -> tuple[bool, str]:
        notional = request.order.quantity * request.order.limit_price
        if request.order.market_id != request.snapshot.market_id:
            return False, "market mismatch"
        if notional > request.limits.max_order_notional:
            return False, "max order notional exceeded"
        if request.current_position_notional + notional > request.limits.max_position_notional:
            return False, "max position notional exceeded"
        if estimated_slippage_bps > request.limits.max_slippage_bps:
            return False, "max slippage exceeded"
        return True, "accepted"


class PaperSimulator:
    def __init__(self, config: SimulationConfig | None = None) -> None:
        self.config = config or SimulationConfig()
        self.risk = RiskEngine()

    def simulate(self, request: SimulationRequest) -> SimulationResult:
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
            filled_quantity=order.quantity,
            fill_price=round(fill_price, 10),
            fee=round(fee, 10),
            slippage_bps=round(slippage_bps, 6),
        )
        return SimulationResult(accepted=True, reason="simulated fill", fill=fill)
