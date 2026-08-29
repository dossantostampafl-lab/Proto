from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from services.market_data.core import compute_orderbook_metrics
from services.quant.core import compute_edge

from .settings import settings
from .surface import _MARKETS, _estimate, _tick

router = APIRouter(tags=["lifecycle"])

_EXPIRY_MINUTES = {
    "btc-threshold": 30,
    "eth-threshold": 60,
    "sol-threshold": 120,
}


def _row(market_id: str) -> dict[str, object]:
    market = _MARKETS[market_id]
    tick = _tick(market)
    orderbook = compute_orderbook_metrics(tick)
    estimate = _estimate(market)
    spread_cost = max(tick.spread, 0.0) / max(tick.bid + tick.ask, 1e-9)
    edge = compute_edge(
        model_probability=estimate.probability,
        market_probability=market.market_probability,
        fees=0.001,
        slippage=0.001,
        spread_cost=spread_cost,
        hedge_cost=0.001,
        uncertainty_penalty=estimate.uncertainty * 0.02,
        latency_penalty=0.0005,
        minimum_edge=settings.minimum_net_edge,
    )
    expiry_minutes = _EXPIRY_MINUTES[market_id]
    expires_at = datetime.now(UTC) + timedelta(minutes=expiry_minutes)
    return {
        "market_id": market.market_id,
        "symbol": market.symbol,
        "source": "SYNTHETIC_DEMO",
        "lifecycle_state": "EDGE_CANDIDATE" if edge.decision == "APPROVE_CANDIDATE" else "ANALYZED",
        "resolution_state": "PENDING",
        "market_probability": market.market_probability,
        "model_probability": estimate.probability,
        "confidence": estimate.confidence,
        "uncertainty": estimate.uncertainty,
        "net_edge": edge.net_edge,
        "edge_decision": edge.decision,
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
        "axes": {
            "radius": "expiry_horizon_minutes",
            "height": "model_probability",
            "intensity": "absolute_net_edge",
        },
        "points": [
            {
                "market_id": row["market_id"],
                "symbol": row["symbol"],
                "expiry_horizon_minutes": row["expiry_horizon_minutes"],
                "model_probability": row["model_probability"],
                "net_edge": row["net_edge"],
                "absolute_net_edge": abs(float(row["net_edge"])),
            }
            for row in rows
        ],
    }
