import pytest
from pydantic import ValidationError

from services.analytics.intelligence import (
    MarketIntelligenceInput,
    OpportunityPolicy,
    RegimePolicy,
    TrendRegime,
    VolatilityRegime,
    classify_market_state,
    rank_opportunities,
)


def _regime_policy() -> RegimePolicy:
    return RegimePolicy(
        trend_threshold=0.01,
        strong_trend_threshold=0.03,
        low_volatility_threshold=0.10,
        high_volatility_threshold=0.30,
    )


def _opportunity_policy() -> OpportunityPolicy:
    return OpportunityPolicy(
        minimum_liquidity=0.50,
        minimum_confidence=0.60,
        minimum_net_edge=0.01,
        minimum_calibration_quality=0.50,
        minimum_risk_quality=0.50,
        edge_scale=0.10,
        weight_edge=1.0,
        weight_confidence=1.0,
        weight_liquidity=1.0,
        weight_calibration=1.0,
        weight_risk=1.0,
    )


def test_market_regime_uses_only_explicit_thresholds() -> None:
    state = classify_market_state(
        MarketIntelligenceInput(
            instrument_id="test:abc",
            return_signal=0.035,
            realized_volatility=0.35,
            liquidity_score=0.9,
            provenance_complete=True,
        ),
        _regime_policy(),
    )
    assert state.instrument_id == "TEST:ABC"
    assert state.trend_regime is TrendRegime.STRONG_UP
    assert state.volatility_regime is VolatilityRegime.HIGH
    assert state.net_edge is None
    assert state.confidence is None


def test_opportunity_ranking_requires_complete_quant_evidence() -> None:
    policy = _opportunity_policy()
    incomplete = classify_market_state(
        MarketIntelligenceInput(
            instrument_id="TEST:INCOMPLETE",
            return_signal=0.02,
            realized_volatility=0.2,
            liquidity_score=0.9,
            confidence=None,
            net_edge=None,
            calibration_quality=None,
            risk_quality=None,
            provenance_complete=True,
        ),
        _regime_policy(),
    )
    assert rank_opportunities([incomplete], policy) == []


def test_opportunity_ranking_rejects_incomplete_provenance() -> None:
    policy = _opportunity_policy()
    state = classify_market_state(
        MarketIntelligenceInput(
            instrument_id="TEST:ABC",
            return_signal=0.02,
            realized_volatility=0.2,
            liquidity_score=0.9,
            confidence=0.8,
            net_edge=0.04,
            calibration_quality=0.8,
            risk_quality=0.8,
            provenance_complete=False,
        ),
        _regime_policy(),
    )
    assert rank_opportunities([state], policy) == []


def test_ranking_is_deterministic_from_supplied_policy() -> None:
    policy = _opportunity_policy()
    states = [
        classify_market_state(
            MarketIntelligenceInput(
                instrument_id="TEST:A",
                return_signal=0.02,
                realized_volatility=0.2,
                liquidity_score=0.8,
                confidence=0.8,
                net_edge=0.03,
                calibration_quality=0.8,
                risk_quality=0.8,
                provenance_complete=True,
            ),
            _regime_policy(),
        ),
        classify_market_state(
            MarketIntelligenceInput(
                instrument_id="TEST:B",
                return_signal=0.02,
                realized_volatility=0.2,
                liquidity_score=0.9,
                confidence=0.9,
                net_edge=0.05,
                calibration_quality=0.9,
                risk_quality=0.9,
                provenance_complete=True,
            ),
            _regime_policy(),
        ),
    ]
    ranked = rank_opportunities(states, policy)
    assert [item.instrument_id for item in ranked] == ["TEST:B", "TEST:A"]
    assert ranked[0].score > ranked[1].score


def test_policies_have_no_implicit_thresholds_or_weights() -> None:
    with pytest.raises(ValidationError):
        RegimePolicy()
    with pytest.raises(ValidationError):
        OpportunityPolicy()
