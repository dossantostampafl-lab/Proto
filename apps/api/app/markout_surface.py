from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from services.market_data.contracts import OrderBookSnapshot, ResearchAsset
from services.market_data.l2_corpus_replay import PublicL2ReplaySnapshot
from services.market_data.markout import (
    DEFAULT_MARKOUT_HORIZONS_MS,
    FillMarkout,
    FillObservation,
    compute_fill_markout,
    summarize_markouts,
)

from .sizing_surface import router as sizing_router

router = APIRouter(tags=["research"])
router.include_router(sizing_router)


class ReplaySnapshotPoint(BaseModel):
    record_index: int = Field(ge=0)
    connection_generation: int = Field(gt=0)
    wire_sequence: int = Field(ge=0)
    snapshot: OrderBookSnapshot


class FillPoint(BaseModel):
    fill_id: str = Field(min_length=1, max_length=160)
    side: Literal["BUY", "SELL"]
    fill_price: float = Field(gt=0)
    filled_at: datetime
    connection_generation: int = Field(gt=0)
    asset: ResearchAsset

    @model_validator(mode="after")
    def validate_timestamp(self) -> FillPoint:
        if self.filled_at.tzinfo is None or self.filled_at.utcoffset() is None:
            raise ValueError("filled_at must be timezone-aware")
        return self


class MarkoutRequest(BaseModel):
    snapshots: list[ReplaySnapshotPoint] = Field(min_length=1, max_length=50_000)
    fills: list[FillPoint] = Field(min_length=1, max_length=10_000)
    horizons_ms: list[int] = Field(
        default_factory=lambda: list(DEFAULT_MARKOUT_HORIZONS_MS),
        min_length=1,
        max_length=32,
    )

    @model_validator(mode="after")
    def validate_horizons_and_clock(self) -> MarkoutRequest:
        if any(value <= 0 for value in self.horizons_ms):
            raise ValueError("markout horizons must contain positive milliseconds")
        if sorted(set(self.horizons_ms)) != self.horizons_ms:
            raise ValueError("markout horizons must be unique and strictly increasing")

        clocks: dict[tuple[int, str], datetime] = {}
        for point in self.snapshots:
            key = (point.connection_generation, point.snapshot.asset)
            previous = clocks.get(key)
            if previous is not None and point.snapshot.observed_at < previous:
                raise ValueError(
                    "L2 snapshots must be ordered by observed_at within "
                    "connection generation and asset"
                )
            clocks[key] = point.snapshot.observed_at
        return self


def _serialize_markout(item: FillMarkout) -> dict[str, object]:
    return {
        "fill_id": item.fill_id,
        "side": item.side,
        "fill_price": item.fill_price,
        "entry_mid": item.entry_mid,
        "spread_capture_bps": item.spread_capture_bps,
        "points": [
            {
                "horizon_ms": point.horizon_ms,
                "observed_at": point.observed_at,
                "future_mid": point.future_mid,
                "markout_bps": point.markout_bps,
                "adverse_selection_bps": point.adverse_selection_bps,
            }
            for point in item.points
        ],
    }


@router.post("/research/execution-quality/markout")
def execution_quality_markout(request: MarkoutRequest) -> dict[str, object]:
    snapshots = tuple(
        PublicL2ReplaySnapshot(
            record_index=point.record_index,
            connection_generation=point.connection_generation,
            wire_sequence=point.wire_sequence,
            snapshot=point.snapshot,
        )
        for point in request.snapshots
    )
    horizons = tuple(request.horizons_ms)

    try:
        markouts = tuple(
            compute_fill_markout(
                snapshots,
                FillObservation(
                    fill_id=fill.fill_id,
                    side=fill.side,
                    fill_price=fill.fill_price,
                    filled_at=fill.filled_at,
                    connection_generation=fill.connection_generation,
                    asset=fill.asset,
                ),
                horizons_ms=horizons,
            )
            for fill in request.fills
        )
        summaries = summarize_markouts(markouts)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {
        "method": "L2_POST_FILL_MARKOUT",
        "fill_count": len(markouts),
        "horizons_ms": list(horizons),
        "markouts": [_serialize_markout(item) for item in markouts],
        "summary": [
            {
                "horizon_ms": item.horizon_ms,
                "observation_count": item.observation_count,
                "mean_markout_bps": item.mean_markout_bps,
                "median_markout_bps": item.median_markout_bps,
                "mean_adverse_selection_bps": item.mean_adverse_selection_bps,
                "adverse_selection_fraction": item.adverse_selection_fraction,
            }
            for item in summaries
        ],
        "connection_boundary_policy": "SAME_CONNECTION_GENERATION_ONLY",
        "financial_connectivity": False,
        "real_money_execution": False,
    }
