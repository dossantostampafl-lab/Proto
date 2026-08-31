from datetime import UTC, datetime, timedelta

from services.quant.pipeline import CalibrationSample, QuantPipelineInput, run_quant_pipeline


def _input() -> QuantPipelineInput:
    observed_at = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    return QuantPipelineInput(
        market_id="btc-threshold-research",
        symbol="BTC",
        observed_at=observed_at,
        market_probability=0.52,
        volatility=0.24,
        imbalance=0.35,
        liquidity_score=0.8,
        fees=0.001,
        slippage=0.0015,
        spread_cost=0.0008,
        hedge_cost=0.0004,
        latency_penalty=0.0003,
        calibration_samples=(
            CalibrationSample(probability=0.50, outcome=0),
            CalibrationSample(probability=0.55, outcome=1),
            CalibrationSample(probability=0.60, outcome=1),
        ),
        calibration_bins=5,
        event_times=(
            observed_at.timestamp() - 2.0,
            observed_at.timestamp() - 1.0,
            observed_at.timestamp() + 10.0,
        ),
        expiry_at=observed_at + timedelta(hours=2),
    )


def test_quant_pipeline_is_deterministic_given_correlation_id() -> None:
    data = _input()

    first = run_quant_pipeline(data, correlation_id="lineage-1")
    second = run_quant_pipeline(data, correlation_id="lineage-1")

    assert first == second
    assert first.observed_at == data.observed_at
    assert first.correlation_id == "lineage-1"
    assert first.model_version
    assert first.feature_version


def test_quant_pipeline_exposes_calibration_edge_and_risk_inputs() -> None:
    result = run_quant_pipeline(_input(), correlation_id="lineage-2")

    assert 0.0 <= result.raw_probability <= 1.0
    assert 0.0 <= result.calibrated_probability <= 1.0
    assert 0.0 <= result.fair_probability <= 1.0
    assert abs(result.confidence - (1.0 - result.uncertainty)) < 1e-12
    assert result.calibration_report is not None
    assert result.calibration_report.count == 3
    assert result.edge.liquidity_penalty > 0.0
    assert result.expected_value.win_probability == result.fair_probability
    assert result.time_exposure.time_to_expiry_seconds == 7_200.0
    assert 0.0 < result.time_exposure.expiry_pressure < 1.0


def test_future_hawkes_events_do_not_leak_into_replay_state() -> None:
    data = _input()
    without_future = data.model_copy(
        update={"event_times": data.event_times[:-1]},
    )

    with_future = run_quant_pipeline(data, correlation_id="same")
    baseline = run_quant_pipeline(without_future, correlation_id="same")

    assert with_future.hawkes == baseline.hawkes


def test_low_liquidity_increases_uncertainty_and_reduces_net_edge() -> None:
    data = _input()
    liquid = run_quant_pipeline(
        data.model_copy(update={"liquidity_score": 1.0}),
        correlation_id="liquid",
    )
    illiquid = run_quant_pipeline(
        data.model_copy(update={"liquidity_score": 0.0}),
        correlation_id="illiquid",
    )

    assert illiquid.uncertainty > liquid.uncertainty
    assert illiquid.edge.liquidity_penalty > liquid.edge.liquidity_penalty
    assert illiquid.edge.net_edge < liquid.edge.net_edge
