from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil, floor, isfinite

from .models import Fill, Side, SimulationRequest, SimulationResult


@dataclass(frozen=True)
class SimulationConfig:
    fee_bps: float = 2.0
    base_slippage_bps: float = 3.0
    latency_ms: float = 25.0
    latency_slippage_bps_per_100ms: float = 0.5
    tick_size: float = 0.01
    depth_impact_bps_at_full_book: float = 8.0
    depth_impact_exponent: float = 1.5
    max_snapshot_age_seconds: float = 10.0
    max_future_skew_seconds: float = 1.0

    def __post_init__(self) -> None:
        values = {
            "fee_bps": self.fee_bps,
            "base_slippage_bps": self.base_slippage_bps,
            "latency_ms": self.latency_ms,
            "latency_slippage_bps_per_100ms": self.latency_slippage_bps_per_100ms,
            "tick_size": self.tick_size,
            "depth_impact_bps_at_full_book": self.depth_impact_bps_at_full_book,
            "depth_impact_exponent": self.depth_impact_exponent,
            "max_snapshot_age_seconds": self.max_snapshot_age_seconds,
            "max_future_skew_seconds": self.max_future_skew_seconds,
        }
        for name, value in values.items():
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.fee_bps < 0:
            raise ValueError("fee_bps must be non-negative")
        if self.base_slippage_bps < 0:
            raise ValueError("base_slippage_bps must be non-negative")
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        if self.latency_slippage_bps_per_100ms < 0:
            raise ValueError("latency_slippage_bps_per_100ms must be non-negative")
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        if self.depth_impact_bps_at_full_book < 0:
            raise ValueError("depth_impact_bps_at_full_book must be non-negative")
        if self.depth_impact_exponent <= 0:
            raise ValueError("depth_impact_exponent must be positive")
        if self.max_snapshot_age_seconds <= 0:
            raise ValueError("max_snapshot_age_seconds must be positive")
        if self.max_future_skew_seconds < 0:
            raise ValueError("max_future_skew_seconds must be non-negative")


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
        risk_reducing = (
            request.current_position_quantity * signed_order_quantity < 0.0
            and abs(signed_order_quantity) <= abs(request.current_position_quantity)
        )
        other_asset_exposure = max(
            request.current_gross_exposure - request.current_asset_exposure,
            0.0,
        )
        projected_gross_exposure = other_asset_exposure + projected_position_notional
        projected_concentration = (
            projected_position_notional / projected_gross_exposure
            if projected_gross_exposure > 0.0
            else 0.0
        )
        if request.order.side == Side.BUY:
            executable_price = request.snapshot.ask
            executable_book_notional = executable_price * request.snapshot.ask_size
        else:
            executable_price = request.snapshot.bid
            executable_book_notional = executable_price * request.snapshot.bid_size
        executable_order_notional = request.order.quantity * executable_price
        allowed_book_ratio = 1.0 if risk_reducing else request.limits.max_order_to_book_ratio
        max_book_supported_notional = executable_book_notional * allowed_book_ratio

        if not request.server_execution_permitted:
            return False, "simulation mode does not permit execution"
        if request.order.market_id != request.snapshot.market_id:
            return False, "market mismatch"
        if order_notional > request.limits.max_order_notional:
            return False, "max order notional exceeded"
        if request.current_drawdown >= request.limits.max_drawdown and not risk_reducing:
            return False, "max drawdown exceeded"
        if projected_position_notional > request.limits.max_position_notional:
            return False, "max position notional exceeded"
        if projected_gross_exposure > request.limits.max_gross_exposure:
            return False, "max gross exposure exceeded"
        if projected_concentration > request.limits.max_asset_concentration:
            return False, "max asset concentration exceeded"
        if request.snapshot.volatility > request.limits.max_volatility and not risk_reducing:
            return False, "max volatility exceeded"
        if executable_order_notional > max_book_supported_notional:
            return False, "insufficient top-of-book liquidity"
        if estimated_slippage_bps > request.limits.max_slippage_bps:
            return False, "max slippage exceeded"
        return True, "accepted"


class PaperSimulator:
    def __init__(self, config: SimulationConfig | None = None) -> None:
        self.config = config or SimulationConfig()
        self.risk = RiskEngine()

    def _validate_snapshot_time(
        self,
        request: SimulationRequest,
        *,
        reference_time: datetime | None = None,
    ) -> tuple[bool, str]:
        observed_at = request.snapshot.observed_at
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            return False, "snapshot timestamp must be timezone-aware"
        clock = reference_time or datetime.now(UTC)
        if clock.tzinfo is None or clock.utcoffset() is None:
            return False, "simulation reference timestamp must be timezone-aware"
        age_seconds = (clock - observed_at).total_seconds()
        if age_seconds > self.config.max_snapshot_age_seconds:
            return False, "stale market snapshot"
        if age_seconds < -self.config.max_future_skew_seconds:
            return False, "market snapshot timestamp is in the future"
        return True, "accepted"

    def _price_on_grid(self, raw_price: float, side: Side) -> float:
        ticks = raw_price / self.config.tick_size
        grid_ticks = ceil(ticks - 1e-12) if side == Side.BUY else floor(ticks + 1e-12)
        return grid_ticks * self.config.tick_size

    def _depth_impact_bps(self, *, order_quantity: float, available_quantity: float) -> float:
        if available_quantity <= 0.0:
            return float("inf")
        participation = max(order_quantity / available_quantity, 0.0)
        return self.config.depth_impact_bps_at_full_book * (
            participation**self.config.depth_impact_exponent
        )

    def simulate(
        self,
        request: SimulationRequest,
        *,
        reference_time: datetime | None = None,
    ) -> SimulationResult:
        snapshot_time_valid, snapshot_time_reason = self._validate_snapshot_time(
            request,
            reference_time=reference_time,
        )
        if not snapshot_time_valid:
            return SimulationResult(accepted=False, reason=snapshot_time_reason)

        order = request.order
        snapshot = request.snapshot
        executable_price = snapshot.ask if order.side == Side.BUY else snapshot.bid
        available_quantity = snapshot.ask_size if order.side == Side.BUY else snapshot.bid_size

        if order.side == Side.BUY and order.limit_price < executable_price:
            return SimulationResult(accepted=False, reason="buy limit below ask")
        if order.side == Side.SELL and order.limit_price > executable_price:
            return SimulationResult(accepted=False, reason="sell limit above bid")

        spread_bps = ((snapshot.ask - snapshot.bid) / executable_price) * 10_000
        latency_slippage_bps = (
            self.config.latency_slippage_bps_per_100ms * self.config.latency_ms / 100.0
        )
        depth_impact_bps = self._depth_impact_bps(
            order_quantity=order.quantity,
            available_quantity=available_quantity,
        )
        slippage_bps = (
            self.config.base_slippage_bps
            + max(spread_bps * 0.10, 0)
            + latency_slippage_bps
            + depth_impact_bps
        )
        accepted, reason = self.risk.validate(request, slippage_bps)
        if not accepted:
            return SimulationResult(accepted=False, reason=reason)

        direction = 1 if order.side == Side.BUY else -1
        raw_fill_price = executable_price * (1 + direction * slippage_bps / 10_000)
        fill_price = self._price_on_grid(raw_fill_price, order.side)
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
