from services.quant.hierarchical_trend import (
    ExpectancySnapshot,
    HierarchicalTrendInput,
    RiskSnapshot,
    SetupSnapshot,
    TimeframeSnapshot,
    TradeDirection,
    evaluate_hierarchical_trend,
)


def _bullish_timeframe() -> TimeframeSnapshot:
    return TimeframeSnapshot(
        close=120.0,
        ema9=115.0,
        ema21=110.0,
        ema50=100.0,
        ema9_slope=0.8,
        ema21_slope=0.7,
        ema50_slope=0.6,
        structure_score=0.9,
        atr=4.0,
    )


def _base_input() -> HierarchicalTrendInput:
    return HierarchicalTrendInput(
        direction=TradeDirection.LONG,
        higher=_bullish_timeframe(),
        middle=_bullish_timeframe(),
        lower=_bullish_timeframe(),
        setup=SetupSnapshot(
            pullback_quality=0.9,
            structure_quality=0.9,
            trigger_quality=0.85,
            volume_zscore=1.5,
        ),
        risk=RiskSnapshot(
            nav=100_000.0,
            entry=112.0,
            structural_invalidation=108.0,
            atr=2.0,
            atr_buffer_multiple=0.5,
            risk_fraction=0.005,
            cluster_risk=0.01,
            max_cluster_risk=0.03,
            portfolio_drawdown=0.02,
            max_portfolio_drawdown=0.15,
        ),
        expectancy=ExpectancySnapshot(
            win_probability=0.42,
            average_win_r=3.5,
            average_loss_r=1.0,
            costs_r=0.05,
        ),
    )


def test_approves_aligned_positive_expectancy_setup() -> None:
    result = evaluate_hierarchical_trend(_base_input())

    assert result.decision == "APPROVED"
    assert result.rejection_reasons == ()
    assert result.regime_score > 0.7
    assert result.stop_price == 107.0
    assert result.stop_distance == 5.0
    assert result.capital_at_risk == 500.0
    assert result.position_units == 100.0
    assert result.expectancy_r > 0.0


def test_rejects_countertrend_long_even_with_good_trigger() -> None:
    bearish = TimeframeSnapshot(
        close=80.0,
        ema9=85.0,
        ema21=90.0,
        ema50=100.0,
        ema9_slope=-0.9,
        ema21_slope=-0.8,
        ema50_slope=-0.7,
        structure_score=-0.9,
        atr=4.0,
    )
    data = _base_input().model_copy(update={"higher": bearish})

    result = evaluate_hierarchical_trend(data)

    assert result.decision == "REJECTED"
    assert "HIGHER_TIMEFRAME_REGIME_MISALIGNED" in result.rejection_reasons
    assert result.position_units == 0.0


def test_rejects_negative_expectancy_without_hardcoded_rr_rule() -> None:
    data = _base_input().model_copy(
        update={
            "expectancy": ExpectancySnapshot(
                win_probability=0.70,
                average_win_r=0.40,
                average_loss_r=1.0,
                costs_r=0.02,
            )
        }
    )

    result = evaluate_hierarchical_trend(data)

    assert result.expectancy_r < 0.0
    assert "NON_POSITIVE_EXPECTANCY" in result.rejection_reasons


def test_rejects_cluster_concentration_and_drawdown_breaches() -> None:
    risk = _base_input().risk.model_copy(
        update={
            "cluster_risk": 0.029,
            "portfolio_drawdown": 0.15,
        }
    )
    data = _base_input().model_copy(update={"risk": risk})

    result = evaluate_hierarchical_trend(data)

    assert result.decision == "REJECTED"
    assert "CLUSTER_RISK_LIMIT" in result.rejection_reasons
    assert "DRAWDOWN_GATE" in result.rejection_reasons
