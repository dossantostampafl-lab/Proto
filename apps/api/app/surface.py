from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from services.analytics.greeks import calculate_synthetic_greeks
from services.hawkes.core import ExponentialHawkesEngine
from services.market_data.core import (
    DataQualityMonitor,
    MarketTick,
    compute_orderbook_metrics,
)
from services.quant.core import compute_edge, estimate_probability
from services.quant.expected_value import calculate_expected_value

from .app_state import portfolio
from .settings import settings

router = APIRouter(tags=["analytics-surface"])


@dataclass(frozen=True)
class SyntheticMarket:
    market_id: str
    symbol: str
    bid: float
    ask: float
    volume: float
    bid_size: float
    ask_size: float
    volatility: float
    market_probability: float


_MARKETS: dict[str, SyntheticMarket] = {
    "btc-threshold": SyntheticMarket(
        market_id="btc-threshold",
        symbol="BTC",
        bid=60_000.0,
        ask=60_010.0,
        volume=125.0,
        bid_size=4.2,
        ask_size=3.8,
        volatility=0.28,
        market_probability=0.52,
    ),
    "eth-threshold": SyntheticMarket(
        market_id="eth-threshold",
        symbol="ETH",
        bid=3_000.0,
        ask=3_002.0,
        volume=850.0,
        bid_size=24.0,
        ask_size=26.0,
        volatility=0.31,
        market_probability=0.49,
    ),
    "sol-threshold": SyntheticMarket(
        market_id="sol-threshold",
        symbol="SOL",
        bid=140.0,
        ask=140.2,
        volume=2_400.0,
        bid_size=180.0,
        ask_size=165.0,
        volatility=0.38,
        market_probability=0.47,
    ),
}


def _require_synthetic_research() -> None:
    if settings.synthetic_research_enabled:
        return
    raise HTTPException(
        status_code=503,
        detail=(
            "synthetic research surface is disabled; use historical replay or "
            "public read-only live market data"
        ),
    )


def _market(market_id: str) -> SyntheticMarket:
    _require_synthetic_research()
    market = _MARKETS.get(market_id.lower())
    if market is None:
        raise HTTPException(status_code=404, detail="market not found")
    return market


def _market_for_symbol(symbol: str) -> SyntheticMarket:
    _require_synthetic_research()
    normalized = symbol.upper()
    for market in _MARKETS.values():
        if market.symbol == normalized:
            return market
    raise HTTPException(status_code=404, detail="symbol not found")


def _tick(market: SyntheticMarket) -> MarketTick:
    return MarketTick(
        timestamp=datetime.now(UTC),
        venue="SYNTHETIC_DEMO",
        symbol=market.symbol,
        bid=market.bid,
        ask=market.ask,
        last=(market.bid + market.ask) / 2.0,
        volume=market.volume,
        bid_size=market.bid_size,
        ask_size=market.ask_size,
        sequence=0,
    )


def _estimate(market: SyntheticMarket):
    imbalance = compute_orderbook_metrics(_tick(market)).imbalance
    return estimate_probability(
        market_probability=market.market_probability,
        volatility=market.volatility,
        imbalance=imbalance,
    )


@router.get("/markets/{market_id}")
def market_detail(market_id: str) -> dict[str, object]:
    market = _market(market_id)
    return {
        "id": market.market_id,
        "asset": market.symbol,
        "state": "ANALYZED",
        "source": "SYNTHETIC_DEMO",
        "real_money_execution": False,
        "market_probability": market.market_probability,
        "volatility": market.volatility,
    }


@router.get("/market-data/{symbol}")
def market_data(symbol: str) -> dict[str, object]:
    market = _market_for_symbol(symbol)
    tick = _tick(market)
    return {
        "timestamp": tick.timestamp,
        "venue": tick.venue,
        "symbol": tick.symbol,
        "bid": tick.bid,
        "ask": tick.ask,
        "mid": tick.mid,
        "last": tick.last,
        "volume": tick.volume,
        "bid_size": tick.bid_size,
        "ask_size": tick.ask_size,
        "spread": tick.spread,
        "sequence": tick.sequence,
        "source": "SYNTHETIC_DEMO",
    }


@router.get("/orderbook/{symbol}")
def orderbook(symbol: str) -> dict[str, object]:
    market = _market_for_symbol(symbol)
    orderbook_metrics = compute_orderbook_metrics(_tick(market))
    return {
        **orderbook_metrics.model_dump(),
        "symbol": market.symbol,
        "source": "SYNTHETIC_DEMO",
    }


@router.get("/data-quality/{symbol}")
def data_quality(symbol: str) -> dict[str, object]:
    market = _market_for_symbol(symbol)
    tick = _tick(market)
    monitor = DataQualityMonitor()
    report = monitor.evaluate(tick, now=tick.timestamp)
    return {
        "symbol": market.symbol,
        "source": "SYNTHETIC_DEMO",
        "valid": report.valid,
        "issues": [issue.value for issue in report.issues],
    }


@router.get("/portfolio")
def canonical_portfolio() -> dict[str, object]:
    snapshot = portfolio.snapshot()
    return {
        **snapshot,
        "source": "SIMULATION_PORTFOLIO",
        "real_money_execution": False,
    }


@router.get("/positions")
def canonical_positions() -> dict[str, object]:
    snapshot = portfolio.snapshot()
    return {
        "mode": snapshot["mode"],
        "source": "SIMULATION_PORTFOLIO",
        "real_money_execution": False,
        "count": len(snapshot["positions"]),
        "positions": snapshot["positions"],
    }


@router.get("/pnl")
def canonical_pnl() -> dict[str, object]:
    snapshot = portfolio.snapshot()
    return {
        "mode": snapshot["mode"],
        "source": "SIMULATION_PORTFOLIO",
        "real_money_execution": False,
        "realized_pnl": snapshot["total_realized_pnl"],
        "unrealized_pnl": snapshot["total_unrealized_pnl"],
        "fees": snapshot["total_fees"],
        "pnl_after_fees": snapshot["total_pnl_after_fees"],
    }


@router.get("/models")
def models() -> list[dict[str, object]]:
    return [
        {
            "name": "baseline-logit-v0",
            "kind": "deterministic_research_baseline",
            "feature_version": "microstructure-v0",
            "trained": False,
            "live_trading_enabled": False,
        }
    ]


@router.get("/models/metrics")
def model_metrics() -> dict[str, object]:
    return {
        "model_version": "baseline-logit-v0",
        "feature_version": "microstructure-v0",
        "status": "RUNTIME_METRICS_EXPOSED_SEPARATELY",
        "runtime_metrics_endpoint": "/metrics",
        "live_trading_enabled": False,
    }


@router.get("/models/calibration")
def model_calibration() -> dict[str, object]:
    return {
        "model_version": "baseline-logit-v0",
        "status": "NOT_COMPUTED",
        "observation_count": 0,
        "brier_score": None,
        "log_loss": None,
        "expected_calibration_error": None,
        "note": (
            "Submit labeled observations to /research/calibration to compute "
            "calibration metrics."
        ),
    }


@router.get("/probability/{market_id}")
def probability_for_market(market_id: str) -> dict[str, object]:
    market = _market(market_id)
    estimate = _estimate(market)
    return {
        "market_id": market.market_id,
        "symbol": market.symbol,
        "source": "SYNTHETIC_DEMO",
        **estimate.model_dump(mode="json"),
    }


@router.get("/edge/{market_id}")
def edge_for_market(market_id: str) -> dict[str, object]:
    market = _market(market_id)
    tick = _tick(market)
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
    return {
        "market_id": market.market_id,
        "symbol": market.symbol,
        "source": "SYNTHETIC_DEMO",
        **edge.model_dump(),
    }


@router.get("/expected-value/{market_id}")
def expected_value_for_market(market_id: str) -> dict[str, object]:
    market = _market(market_id)
    estimate = _estimate(market)
    contract_price = market.market_probability
    result = calculate_expected_value(
        win_probability=estimate.probability,
        profit_if_win=1.0 - contract_price,
        loss_if_lose=contract_price,
        fees=0.001,
        slippage=0.001,
        spread_cost=0.001,
        hedge_cost=0.001,
        latency_cost=0.0005,
        uncertainty_penalty=estimate.uncertainty * 0.02,
    )
    return {
        "market_id": market.market_id,
        "symbol": market.symbol,
        "source": "SYNTHETIC_DEMO",
        "contract_price": contract_price,
        **result.model_dump(),
    }


@router.get("/analytics/greeks/{market_id}")
def synthetic_greeks_for_market(market_id: str) -> dict[str, object]:
    market = _market(market_id)
    imbalance = compute_orderbook_metrics(_tick(market)).imbalance
    greeks = calculate_synthetic_greeks(
        market_probability=market.market_probability,
        volatility=market.volatility,
        imbalance=imbalance,
    )
    return {
        "market_id": market.market_id,
        "symbol": market.symbol,
        "source": "SYNTHETIC_MODEL_SENSITIVITY",
        "definition": {
            "market_probability_delta": "d(model_probability)/d(market_probability)",
            "volatility_vega": "d(model_probability)/d(volatility)",
            "imbalance_kappa": "d(model_probability)/d(orderbook_imbalance)",
            "time_theta": "0 because baseline-logit-v0 has no time input",
        },
        **greeks.__dict__,
    }


@router.get("/hawkes/{symbol}")
def hawkes_for_symbol(symbol: str) -> dict[str, object]:
    market = _market_for_symbol(symbol)
    engine = ExponentialHawkesEngine(mu=0.20, alpha=0.30, beta=1.20)
    estimate = engine.estimate(timestamp=0.0, horizon=1.0)
    return {
        "symbol": market.symbol,
        "source": "SYNTHETIC_RESEARCH_BASELINE",
        "event_count": 0,
        "baseline_intensity": estimate.baseline_intensity,
        "current_intensity": estimate.current_intensity,
        "excitation": estimate.excitation,
        "decay": estimate.decay,
        "branching_ratio": estimate.branching_ratio,
        "event_probability": estimate.event_probability,
    }
