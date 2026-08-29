from __future__ import annotations

from .models import EdgeEstimate, EdgeRequest


def estimate_binary_edge(request: EdgeRequest) -> EdgeEstimate:
    market_mid = (request.yes_bid + request.yes_ask) / 2
    edge = request.fair_probability - market_mid

    if edge > 0:
        side = "YES"
    elif edge < 0:
        side = "NO"
    else:
        side = "NEUTRAL"

    return EdgeEstimate(
        market_id=request.market_id,
        market_mid_probability=round(market_mid, 8),
        fair_probability=round(request.fair_probability, 8),
        edge=round(edge, 8),
        edge_bps=round(edge * 10_000, 4),
        side=side,
    )
