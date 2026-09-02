from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from services.market_data.core import compute_orderbook_metrics
from services.quant.core import compute_edge, estimate_probability

from .models import MarketSnapshot
from .settings import settings
from .surface import _MARKETS, _estimate, _require_synthetic_research, _tick

router = APIRouter(tags=["lifecycle"])

_EXPIRY_MINUTES = {
    "btc-threshold": 30,
    "eth-threshold": 60,
    "sol-threshold": 120,
}

_UNAVAILABLE_EXECUTION_COSTS = (
    "fees",
    "slippage",
    "hedge_cost",
    "latency_penalty",
)


@router.post("/edge/evaluate")
def evaluate_edge_with_explicit_costs(
    snapshot: MarketSnapshot,
    fees: float | None = Query(default=None, ge=0.0),
    slippage: float | None = Query(default=None, ge=0.0),
    hedge_cost: float | None = Query(default=None, ge=0.0),
    latency_penalty: float | None = Query(default=None, ge=0.0),
) -> dict[str, object]:
    provided = {
        "fees": fees,
        "slippage": slippage,
        "hedge_cost": hedge_cost,
        "latency_penalty": latency_penalty,
    }
    unavailable = [name for name, value in provided.items() if value is None]
    if unavailable:
        raise HTTPException(
            status_code=422,
            detail={
                "status": "COSTS_UNAVAILABLE",
                "cost_policy": "EXPLICIT_EXECUTION_COSTS_REQUIRED",
                "unavailable_costs": unavailable,
            },
        )

    estimate = estimate_probability(
        market_probability=snapshot.market_probability,
        volatility=snapshot.volatility,
        imbalance=snapshot.imbalance,
    )
    spread_cost = max(snapshot.ask - snapshot.bid, 0.0) / max(
        snapshot.ask + snapshot.bid,
        1e-9,
    )
    uncertainty_penalty = estimate.uncertainty * 0.02
    result = compute_edge(
        model_probability=estimate.probability,
        market_probability=snapshot.market_probability,
        fees=float(fees),
        slippage=float(slippage),
        spread_cost=spread_cost,
        hedge_cost=float(hedge_cost),
        uncertainty_penalty=uncertainty_penalty,
        latency_penalty=float(latency_penalty),
        minimum_edge=settings.minimum_net_edge,
    )
    return {
        **result.model_dump(),
        "cost_policy": "EXPLICIT_EXECUTION_COSTS",
        "costs_complete": True,
        "known_costs": {
            "fees": fees,
            "slippage": slippage,
            "spread_cost": spread_cost,
            "hedge_cost": hedge_cost,
            "latency_penalty": latency_penalty,
            "uncertainty_penalty": uncertainty_penalty,
        },
    }


def _row(market_id: str) -> dict[str, object]:
    _require_synthetic_research()
    market = _MARKETS[market_id]
    tick = _tick(market)
    orderbook = compute_orderbook_metrics(tick)
    estimate = _estimate(market)
    spread_cost = max(tick.spread, 0.0) / max(tick.bid + tick.ask, 1e-9)
    uncertainty_penalty = estimate.uncertainty * 0.02

    # Only costs derivable from the synthetic quote/model are included here.
    # Fees, slippage, hedge cost and latency are deliberately not invented.
    partial_edge = compute_edge(
        model_probability=estimate.probability,
        market_probability=market.market_probability,
        fees=0.0,
        slippage=0.0,
        spread_cost=spread_cost,
        hedge_cost=0.0,
        uncertainty_penalty=uncertainty_penalty,
        latency_penalty=0.0,
        minimum_edge=settings.minimum_net_edge,
    )
    expiry_minutes = _EXPIRY_MINUTES[market_id]
    expires_at = datetime.now(UTC) + timedelta(minutes=expiry_minutes)
    return {
        "market_id": market.market_id,
        "symbol": market.symbol,
        "source": "SYNTHETIC_DEMO",
        "lifecycle_state": "ANALYZED",
        "resolution_state": "PENDING",
        "market_probability": market.market_probability,
        "model_probability": estimate.probability,
        "confidence": estimate.confidence,
        "uncertainty": estimate.uncertainty,
        "net_edge": partial_edge.net_edge,
        "net_edge_is_partial": True,
        "edge_decision": "COSTS_UNAVAILABLE",
        "cost_policy": "PARTIAL_DERIVED_COSTS_ONLY",
        "known_costs": {
            "spread_cost": spread_cost,
            "uncertainty_penalty": uncertainty_penalty,
        },
        "unavailable_costs": list(_UNAVAILABLE_EXECUTION_COSTS),
        "liquidity_depth": orderbook.depth,
        "imbalance": orderbook.imbalance,
        "expiry_horizon_minutes": expiry_minutes,
        "synthetic_expires_at": expires_at,
        "real_money_execution": False,
    }


@router.get("/market-lifecycle")
def market_lifecycle() -> dict[str, object]:
    rows = [_row(market_id) for market_id in _MARKETS]
    return {
        "source": "SYNTHETIC_DEMO",
        "count": len(rows),
        "markets": rows,
    }


@router.get("/resolution-grid")
def resolution_grid() -> dict[str, object]:
    rows = [_row(market_id) for market_id in _MARKETS]
    return {
        "source": "SYNTHETIC_DEMO",
        "resolution_policy": "PENDING_SYNTHETIC_DEMO_ONLY",
        "markets": [
            {
                "market_id": row["market_id"],
                "symbol": row["symbol"],
                "resolution_state": row["resolution_state"],
                "market_probability": row["market_probability"],
                "model_probability": row["model_probability"],
                "net_edge": row["net_edge"],
                "net_edge_is_partial": row["net_edge_is_partial"],
                "edge_decision": row["edge_decision"],
                "unavailable_costs": row["unavailable_costs"],
                "expiry_horizon_minutes": row["expiry_horizon_minutes"],
            }
            for row in rows
        ],
    }


@router.get("/analytics/expiry-map")
def expiry_map() -> dict[str, object]:
    rows = [_row(market_id) for market_id in _MARKETS]
    return {
        "source": "SYNTHETIC_DEMO",
        "edge_policy": "PARTIAL_DERIVED_COSTS_ONLY",
        "axes": {
            "radius": "expiry_horizon_minutes",
            "height": "model_probability",
            "intensity": "absolute_partial_net_edge",
        },
        "points": [
            {
                "market_id": row["market_id"],
                "symbol": row["symbol"],
                "expiry_horizon_minutes": row["expiry_horizon_minutes"],
                "model_probability": row["model_probability"],
                "net_edge": row["net_edge"],
                "net_edge_is_partial": row["net_edge_is_partial"],
                "absolute_partial_net_edge": abs(float(row["net_edge"])),
            }
            for row in rows
        ],
    }
