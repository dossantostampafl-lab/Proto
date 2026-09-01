from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from services.validation import (
    PromotionGateEvidence,
    PromotionGatePolicy,
    evaluate_promotion_gate,
)

from .app_state import persistence_engine
from .metrics_state import metrics
from .research_persistence import persist_model_promotion_decision

router = APIRouter(prefix="/research/validation", tags=["research", "validation"])


def _json_safe(value: object) -> object:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


class PromotionEvidenceRequest(BaseModel):
    experiment_id: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    candidate_kind: Literal["CONTROL", "ALPHA_CANDIDATE"]
    oos_sample_count: int = Field(ge=0)
    validation_fold_count: int = Field(ge=0)
    cumulative_return: float = Field(allow_inf_nan=False)
    sharpe: float = Field(allow_inf_nan=False)
    max_drawdown: float = Field(ge=0.0, allow_inf_nan=False)
    positive_fold_fraction: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    robustness_score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    deflated_sharpe_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    probability_of_backtest_overfitting: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    monte_carlo_probability_of_loss: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    regime_robustness_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    parameter_stability_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    delay_control_sharpe: float | None = Field(default=None, allow_inf_nan=False)
    shuffle_control_sharpe: float | None = Field(default=None, allow_inf_nan=False)
    family_reality_check_p_value: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    family_spa_p_value: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    frozen_holdout_passed: bool = False
    frozen_holdout_consumed: bool = False
    frozen_holdout_seal_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("experiment_id")
    @classmethod
    def normalize_experiment_id(cls, value: str) -> str:
        return value.lower()


class PromotionPolicyRequest(BaseModel):
    min_oos_samples: int = Field(default=250, gt=0)
    min_validation_folds: int = Field(default=5, gt=0)
    min_cumulative_return: float = Field(default=0.0, allow_inf_nan=False)
    min_sharpe: float = Field(default=0.25, allow_inf_nan=False)
    min_positive_fold_fraction: float = Field(default=0.60, ge=0.0, le=1.0)
    min_robustness_score: float = Field(default=0.60, ge=0.0, le=1.0)
    min_deflated_sharpe_ratio: float = Field(default=0.95, ge=0.0, le=1.0)
    max_probability_of_backtest_overfitting: float = Field(default=0.20, ge=0.0, le=1.0)
    max_drawdown: float = Field(default=0.20, ge=0.0, le=1.0)
    max_monte_carlo_probability_of_loss: float = Field(default=0.35, ge=0.0, le=1.0)
    min_regime_robustness_score: float = Field(default=0.60, ge=0.0, le=1.0)
    min_parameter_stability_score: float = Field(default=0.60, ge=0.0, le=1.0)
    max_negative_control_sharpe_ratio: float = Field(default=0.75, ge=0.0, le=1.0)
    max_family_reality_check_p_value: float = Field(default=0.05, ge=0.0, le=1.0)
    max_family_spa_p_value: float = Field(default=0.05, ge=0.0, le=1.0)


class PromotionEvaluationRequest(BaseModel):
    evidence: PromotionEvidenceRequest
    policy: PromotionPolicyRequest = Field(default_factory=PromotionPolicyRequest)


@router.post("/promotion/evaluate")
async def promotion_evaluate_endpoint(
    request: PromotionEvaluationRequest,
) -> dict[str, object]:
    try:
        evidence = PromotionGateEvidence(**request.evidence.model_dump())
        policy = PromotionGatePolicy(**request.policy.model_dump())
        decision = evaluate_promotion_gate(evidence, policy=policy)
    except ValueError as error:
        metrics.increment("model_promotion_rejected")
        raise HTTPException(status_code=422, detail=str(error)) from error

    metrics.increment("model_promotion_requests")
    if decision.status == "PAPER_TRADING_ELIGIBLE":
        metrics.increment("model_promotion_paper_trading_eligible")
    elif decision.status == "CONTROL_ONLY":
        metrics.increment("model_promotion_control_only")
    else:
        metrics.increment("model_promotion_research_only")

    decision_payload: dict[str, object] = {
        "experiment_id": decision.experiment_id,
        "status": decision.status,
        "promotion_eligible": decision.promotion_eligible,
        "checks": _json_safe(decision.checks),
        "failed_checks": list(decision.failed_checks),
        "decision_fingerprint": decision.decision_fingerprint,
        "paper_trading_only": decision.paper_trading_only,
        "live_execution_eligible": decision.live_execution_eligible,
        "financial_connectivity": decision.financial_connectivity,
        "real_money_execution": decision.real_money_execution,
    }
    persistence_payload: dict[str, object] = {
        "record_type": "model_promotion_decision",
        "experiment_id": decision.experiment_id,
        "evidence": request.evidence.model_dump(mode="json"),
        "policy": request.policy.model_dump(mode="json"),
        "decision": decision_payload,
    }
    try:
        promotion_record_id, persisted = await persist_model_promotion_decision(
            persistence_engine,
            experiment_id=decision.experiment_id,
            decision_fingerprint=decision.decision_fingerprint,
            payload=persistence_payload,
        )
    except RuntimeError as error:
        metrics.increment("model_promotion_collision")
        raise HTTPException(status_code=409, detail=str(error)) from error

    if persisted:
        metrics.increment("model_promotion_persisted")

    return {
        **decision_payload,
        "policy": request.policy.model_dump(mode="json"),
        "promotion_record_id": promotion_record_id,
        "persisted": persisted,
    }
