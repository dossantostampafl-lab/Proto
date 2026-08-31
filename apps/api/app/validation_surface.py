from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from services.validation import (
    ParameterPoint,
    deflated_sharpe_ratio,
    monte_carlo_block_bootstrap,
    parameter_stability,
    probability_of_backtest_overfitting,
    purged_walk_forward_splits,
    regime_robustness,
    validation_report,
)

from .metrics_state import metrics

router = APIRouter(prefix="/research/validation", tags=["research", "validation"])


class ParameterPointRequest(BaseModel):
    parameter: float
    score: float


class ValidationRequest(BaseModel):
    returns: list[float] = Field(min_length=6)
    train_size: int = Field(gt=0)
    test_size: int = Field(gt=0)
    purge_size: int = Field(default=0, ge=0)
    embargo_size: int = Field(default=0, ge=0)
    step_size: int | None = Field(default=None, gt=0)
    trials: int = Field(default=1, gt=0)
    monte_carlo_simulations: int = Field(default=500, ge=10, le=20_000)
    monte_carlo_block_size: int = Field(default=2, gt=0)
    monte_carlo_seed: int = 7
    regimes: list[str] | None = None
    parameter_points: list[ParameterPointRequest] | None = None
    parameter_relative_tolerance: float = Field(default=0.10, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_optional_dimensions(self) -> ValidationRequest:
        if self.regimes is not None and len(self.regimes) != len(self.returns):
            raise ValueError("regimes must have the same length as returns")
        if self.monte_carlo_block_size > len(self.returns):
            raise ValueError("monte_carlo_block_size must not exceed returns length")
        return self


class PboRequest(BaseModel):
    strategy_returns: list[list[float]] = Field(min_length=2)
    segments: int = Field(default=8, ge=4)


@router.post("/report")
def validation_report_endpoint(request: ValidationRequest) -> dict[str, object]:
    returns = tuple(request.returns)
    folds = purged_walk_forward_splits(
        len(returns),
        train_size=request.train_size,
        test_size=request.test_size,
        purge_size=request.purge_size,
        embargo_size=request.embargo_size,
        step_size=request.step_size,
    )
    report = validation_report(returns, folds)
    monte_carlo = monte_carlo_block_bootstrap(
        returns,
        simulations=request.monte_carlo_simulations,
        block_size=request.monte_carlo_block_size,
        seed=request.monte_carlo_seed,
    )

    regime_report = (
        regime_robustness(returns, tuple(request.regimes))
        if request.regimes is not None
        else None
    )
    stability_report = (
        parameter_stability(
            tuple(
                ParameterPoint(parameter=item.parameter, score=item.score)
                for item in request.parameter_points
            ),
            relative_tolerance=request.parameter_relative_tolerance,
        )
        if request.parameter_points is not None
        else None
    )

    metrics.increment("validation_report_requests")
    return {
        "fold_count": len(folds),
        "performance": report.metrics.__dict__,
        "positive_fold_fraction": report.positive_fold_fraction,
        "worst_fold_return": report.worst_fold_return,
        "median_fold_return": report.median_fold_return,
        "robustness_score": report.robustness_score,
        "deflated_sharpe_ratio": deflated_sharpe_ratio(returns, trials=request.trials),
        "monte_carlo": monte_carlo.__dict__,
        "regime": regime_report.__dict__ if regime_report is not None else None,
        "parameter_stability": (
            stability_report.__dict__ if stability_report is not None else None
        ),
        "financial_connectivity": False,
        "real_money_execution": False,
    }


@router.post("/pbo")
def pbo_endpoint(request: PboRequest) -> dict[str, object]:
    metrics.increment("validation_pbo_requests")
    return {
        "probability_of_backtest_overfitting": probability_of_backtest_overfitting(
            tuple(tuple(values) for values in request.strategy_returns),
            segments=request.segments,
        ),
        "strategy_count": len(request.strategy_returns),
        "financial_connectivity": False,
        "real_money_execution": False,
    }
