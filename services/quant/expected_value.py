from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ExpectedValueResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    win_probability: float = Field(ge=0.0, le=1.0)
    expected_profit: float
    expected_loss: float
    ev: float
    ev_after_costs: float
    risk_adjusted_ev: float
    total_costs: float = Field(ge=0.0)
    risk_penalty: float = Field(ge=0.0)


def calculate_expected_value(
    *,
    win_probability: float,
    profit_if_win: float,
    loss_if_lose: float,
    fees: float = 0.0,
    slippage: float = 0.0,
    spread_cost: float = 0.0,
    hedge_cost: float = 0.0,
    latency_cost: float = 0.0,
    uncertainty_penalty: float = 0.0,
) -> ExpectedValueResult:
    if not 0.0 <= win_probability <= 1.0:
        raise ValueError("win_probability must be between zero and one")
    if profit_if_win < 0.0:
        raise ValueError("profit_if_win must be non-negative")
    if loss_if_lose < 0.0:
        raise ValueError("loss_if_lose must be non-negative")

    costs = [fees, slippage, spread_cost, hedge_cost, latency_cost]
    if any(value < 0.0 for value in costs):
        raise ValueError("execution costs must be non-negative")
    if uncertainty_penalty < 0.0:
        raise ValueError("uncertainty_penalty must be non-negative")

    expected_profit = win_probability * profit_if_win
    expected_loss = (1.0 - win_probability) * loss_if_lose
    ev = expected_profit - expected_loss
    total_costs = sum(costs)
    ev_after_costs = ev - total_costs
    risk_adjusted_ev = ev_after_costs - uncertainty_penalty

    return ExpectedValueResult(
        win_probability=win_probability,
        expected_profit=expected_profit,
        expected_loss=expected_loss,
        ev=ev,
        ev_after_costs=ev_after_costs,
        risk_adjusted_ev=risk_adjusted_ev,
        total_costs=total_costs,
        risk_penalty=uncertainty_penalty,
    )
