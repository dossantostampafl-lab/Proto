from __future__ import annotations

from pydantic import BaseModel, Field


class HedgeExposure(BaseModel):
    gross_exposure: float = Field(ge=0)
    net_exposure: float
    btc_beta: float = 0.0
    eth_beta: float = 0.0
    sol_beta: float = 0.0
    expiry_exposure: float = 0.0
    probability_exposure: float = 0.0


class HedgeRequest(BaseModel):
    desired_alpha_exposure: float
    current_directional_exposure: float
    hedge_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    max_hedge_notional: float = Field(gt=0)


class HedgePlan(BaseModel):
    simulated_only: bool = True
    unwanted_directional_exposure: float
    target_hedge_notional: float
    residual_directional_exposure: float


def build_simulated_hedge(request: HedgeRequest) -> HedgePlan:
    unwanted = request.current_directional_exposure - request.desired_alpha_exposure
    target = -unwanted * request.hedge_ratio
    target = max(min(target, request.max_hedge_notional), -request.max_hedge_notional)
    residual = request.current_directional_exposure + target - request.desired_alpha_exposure
    return HedgePlan(
        unwanted_directional_exposure=unwanted,
        target_hedge_notional=target,
        residual_directional_exposure=residual,
    )
