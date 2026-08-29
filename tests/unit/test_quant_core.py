from services.quant.core import compute_edge, estimate_probability


def test_probability_is_bounded_and_confidence_matches_uncertainty():
    result = estimate_probability(market_probability=0.53, volatility=0.25, imbalance=0.4)
    assert 0.0 <= result.probability <= 1.0
    assert 0.0 <= result.confidence <= 1.0
    assert 0.0 <= result.uncertainty <= 1.0
    assert abs(result.confidence - (1.0 - result.uncertainty)) < 1e-12


def test_edge_subtracts_all_costs():
    result = compute_edge(
        model_probability=0.61,
        market_probability=0.53,
        fees=0.005,
        slippage=0.004,
        spread_cost=0.003,
        hedge_cost=0.002,
        uncertainty_penalty=0.006,
        latency_penalty=0.001,
        minimum_edge=0.01,
    )
    assert abs(result.raw_edge - 0.08) < 1e-12
    assert abs(result.net_edge - 0.059) < 1e-12
    assert result.decision == "APPROVE_CANDIDATE"


def test_negative_or_small_net_edge_is_rejected():
    result = compute_edge(
        model_probability=0.54,
        market_probability=0.53,
        fees=0.003,
        slippage=0.003,
        spread_cost=0.003,
        hedge_cost=0.002,
        uncertainty_penalty=0.002,
        latency_penalty=0.001,
        minimum_edge=0.01,
    )
    assert result.decision == "REJECT"
