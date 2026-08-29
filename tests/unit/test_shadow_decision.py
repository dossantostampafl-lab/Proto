from services.shadow import ShadowDecisionEngine


def test_shadow_engine_records_hypothetical_action_without_execution() -> None:
    decision = ShadowDecisionEngine(
        minimum_abs_edge=0.01,
        minimum_confidence=0.60,
    ).evaluate(
        symbol="BTC",
        model_probability=0.62,
        market_probability=0.56,
        net_edge=0.04,
        confidence=0.83,
    )

    assert decision.action == "WOULD_BUY"
    assert decision.creates_fill is False
    assert decision.submits_external_order is False


def test_shadow_engine_observes_when_thresholds_are_not_met() -> None:
    decision = ShadowDecisionEngine().evaluate(
        symbol="ETH",
        model_probability=0.51,
        market_probability=0.50,
        net_edge=0.005,
        confidence=0.90,
    )

    assert decision.action == "OBSERVE"
    assert decision.creates_fill is False
    assert decision.submits_external_order is False
