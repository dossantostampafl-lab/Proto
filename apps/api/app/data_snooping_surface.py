from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from services.validation import superior_predictive_ability

from .holdout_surface import router as holdout_router
from .metrics_state import metrics

router = APIRouter(prefix="/research/validation", tags=["research", "validation"])
router.include_router(holdout_router)


class DataSnoopingRequest(BaseModel):
    strategy_returns: list[list[float]] = Field(min_length=2)
    benchmark_returns: list[float] = Field(min_length=4)
    simulations: int = Field(default=1_000, ge=100, le=20_000)
    block_size: int = Field(default=5, gt=0)
    seed: int = 7

    @model_validator(mode="after")
    def validate_matrix(self) -> DataSnoopingRequest:
        sample_count = len(self.benchmark_returns)
        if any(len(values) != sample_count for values in self.strategy_returns):
            raise ValueError("strategy and benchmark series must have equal length")
        if self.block_size > sample_count:
            raise ValueError("block_size must not exceed sample count")
        return self


@router.post("/data-snooping")
def data_snooping_endpoint(request: DataSnoopingRequest) -> dict[str, object]:
    try:
        report = superior_predictive_ability(
            tuple(tuple(values) for values in request.strategy_returns),
            tuple(request.benchmark_returns),
            simulations=request.simulations,
            block_size=request.block_size,
            seed=request.seed,
        )
    except ValueError as error:
        metrics.increment("validation_data_snooping_rejected")
        raise HTTPException(status_code=422, detail=str(error)) from error

    metrics.increment("validation_data_snooping_requests")
    return {
        **asdict(report),
        "method": "WHITE_REALITY_CHECK_HANSEN_SPA",
        "financial_connectivity": False,
        "real_money_execution": False,
    }
