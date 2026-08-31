from __future__ import annotations

from .models import RiskLimits, SimulationRequest
from .risk_state import simulation_execution_allowed


def authoritative_simulation_request(
    request: SimulationRequest,
    portfolio_snapshot: dict[str, object],
    *,
    max_order_notional: float,
    max_position_notional: float,
    max_slippage_bps: float,
) -> SimulationRequest:
    """Apply server-side simulation risk authority without blocking stricter client limits."""

    mid_price = (request.snapshot.bid + request.snapshot.ask) / 2.0
    canonical_position_quantity = 0.0
    positions = portfolio_snapshot.get("positions", [])
    if isinstance(positions, list):
        for position in positions:
            if not isinstance(position, dict):
                continue
            if str(position.get("asset")) != request.order.asset.value:
                continue
            canonical_position_quantity = float(position.get("quantity", 0.0))
            break

    canonical_position_notional = abs(canonical_position_quantity) * mid_price
    requested_limits = request.limits
    effective_limits = RiskLimits(
        max_order_notional=min(requested_limits.max_order_notional, max_order_notional),
        max_position_notional=min(
            requested_limits.max_position_notional,
            max_position_notional,
        ),
        max_slippage_bps=min(requested_limits.max_slippage_bps, max_slippage_bps),
    )
    return request.model_copy(
        update={
            "current_position_notional": canonical_position_notional,
            "current_position_quantity": canonical_position_quantity,
            "limits": effective_limits,
            "server_execution_permitted": simulation_execution_allowed(),
        }
    )


__all__ = ["authoritative_simulation_request"]
