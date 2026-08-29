import pytest

from services.quant.expected_value import calculate_expected_value


def test_expected_value_applies_costs_and_risk_penalty() -> None:
    result = calculate_expected_value(
        win_probability=0.60,
        profit_if_win=1.0,
        loss_if_lose=1.0,
        fees=0.01,
        slippage=0.01,
        spread_cost=0.01,
        hedge_cost=0.01,
        latency_cost=0.005,
        uncertainty_penalty=0.02,
    )

    assert result.expected_profit == pytest.approx(0.60)
    assert result.expected_loss == pytest.approx(0.40)
    assert result.ev == pytest.approx(0.20)
    assert result.total_costs == pytest.approx(0.045)
    assert result.ev_after_costs == pytest.approx(0.155)
    assert result.risk_adjusted_ev == pytest.approx(0.135)


def test_expected_value_rejects_invalid_probability() -> None:
    with pytest.raises(ValueError, match="between zero and one"):
        calculate_expected_value(
            win_probability=1.1,
            profit_if_win=1.0,
            loss_if_lose=1.0,
        )


def test_expected_value_rejects_negative_costs() -> None:
    with pytest.raises(ValueError, match="costs must be non-negative"):
        calculate_expected_value(
            win_probability=0.5,
            profit_if_win=1.0,
            loss_if_lose=1.0,
            fees=-0.01,
        )
