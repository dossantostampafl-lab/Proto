from __future__ import annotations

from .models import RiskLimits, SimulationRequest
from .risk_state import simulation_execution_allowed
from .settings import settings


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
    canonical_gross_exposure = float(portfolio_snapshot.get("gross_exposure", 0.0))
    canonical_asset_exposure = 0.0
    exposure_by_asset = portfolio_snapshot.get("exposure_by_asset", {})
    if isinstance(exposure_by_asset, dict):
        canonical_asset_exposure = float(
            exposure_by_asset.get(request.order.asset.value, 0.0)
        )
    canonical_total_pnl = float(portfolio_snapshot.get("total_pnl_after_fees", 0.0))
    canonical_drawdown = max(-canonical_total_pnl, 0.0)

    requested_limits = request.limits
    effective_limits = RiskLimits(
        max_order_notional=min(requested_limits.max_order_notional, max_order_notional),
        max_position_notional=min(
            requested_limits.max_position_notional,
            max_position_notional,
        ),
        max_slippage_bps=min(requested_limits.max_slippage_bps, max_slippage_bps),
        max_gross_exposure=min(
            requested_limits.max_gross_exposure,
            settings.simulation_max_gross_exposure,
        ),
        max_asset_concentration=min(
            requested_limits.max_asset_concentration,
            settings.simulation_max_asset_concentration,
        ),
        max_drawdown=min(requested_limits.max_drawdown, settings.max_daily_drawdown),
    )
    return request.model_copy(
        update={
            "current_position_notional": canonical_position_notional,
            "current_position_quantity": canonical_position_quantity,
            "current_gross_exposure": canonical_gross_exposure,
            "current_asset_exposure": canonical_asset_exposure,
            "current_drawdown": canonical_drawdown,
            "limits": effective_limits,
            "server_execution_permitted": simulation_execution_allowed(),
        }
    )


__all__ = ["authoritative_simulation_request"]
