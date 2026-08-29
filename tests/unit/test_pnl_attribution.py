from services.analytics.pnl_attribution import PnLAttributionInput, attribute_pnl


def test_pnl_attribution_reconciles_to_observed_total() -> None:
    result = attribute_pnl(
        PnLAttributionInput(
            model_edge=120,
            market_movement=40,
            execution=10,
            spread_capture=5,
            slippage=-12,
            fees=-8,
            hedging=-15,
            timing=7,
            observed_total_pnl=130,
        )
    )
    assert result.attributed_total == 130
    assert result.residual == -17


def test_cost_components_can_be_negative_without_breaking_reconciliation() -> None:
    result = attribute_pnl(
        PnLAttributionInput(
            slippage=-20,
            fees=-10,
            hedging=-5,
            observed_total_pnl=-40,
        )
    )
    assert result.residual == -5
    assert result.attributed_total == result.observed_total_pnl
