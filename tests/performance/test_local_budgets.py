from datetime import UTC, datetime
from time import perf_counter

from services.market_data.core import MarketTick, compute_orderbook_metrics
from services.quant.core import compute_edge, estimate_probability


def _duration_ms(callable_) -> float:
    started = perf_counter()
    callable_()
    return (perf_counter() - started) * 1_000.0


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    return ordered[index]


def test_market_data_normalization_path_stays_below_local_budget() -> None:
    tick = MarketTick(
        timestamp=datetime.now(UTC),
        venue="PERF_TEST",
        symbol="BTC",
        bid=60_000.0,
        ask=60_010.0,
        last=60_005.0,
        volume=100.0,
        bid_size=4.0,
        ask_size=3.0,
        sequence=1,
    )

    samples = [_duration_ms(lambda: compute_orderbook_metrics(tick)) for _ in range(1_000)]

    assert _p95(samples) < 10.0


def test_probability_and_edge_path_stays_below_local_budget() -> None:
    def calculate() -> None:
        estimate = estimate_probability(
            market_probability=0.52,
            volatility=0.28,
            imbalance=0.05,
        )
        compute_edge(
            model_probability=estimate.probability,
            market_probability=0.52,
            fees=0.001,
            slippage=0.001,
            spread_cost=0.001,
            hedge_cost=0.001,
            uncertainty_penalty=estimate.uncertainty * 0.02,
            latency_penalty=0.0005,
            minimum_edge=0.01,
        )

    samples = [_duration_ms(calculate) for _ in range(1_000)]

    assert _p95(samples) < 10.0
