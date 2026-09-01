from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from services.portfolio.sizing import SizingInput, SizingMethod, size_position

router = APIRouter(tags=["research"])


class PositionSizingRequest(BaseModel):
    method: SizingMethod
    inputs: SizingInput


@router.post("/research/portfolio/size")
def research_position_size(request: PositionSizingRequest) -> dict[str, object]:
    result = size_position(request.method, request.inputs)
    return {
        **result.model_dump(mode="json"),
        "hard_notional_cap": request.inputs.hard_notional_cap,
        "research_only": request.method == SizingMethod.CAPPED_KELLY_RESEARCH_ONLY,
        "financial_connectivity": False,
        "real_money_execution": False,
    }
