from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from math import isfinite

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from services.validation.experiments import stable_fingerprint
from services.validation.holdout import (
    FrozenHoldoutEvidence,
    FrozenHoldoutPolicy,
    FrozenHoldoutSeal,
    evaluate_frozen_holdout,
)

from .app_state import persistence_engine
from .holdout_persistence import (
    consume_frozen_holdout_decision,
    load_frozen_holdout_seal,
    persist_frozen_holdout_seal,
)
from .metrics_state import metrics

router = APIRouter(prefix="/holdout", tags=["research", "validation", "holdout"])


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _fingerprint(payload: dict[str, object]) -> str:
    return stable_fingerprint(payload)


def _safe_metrics(value: object) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, item in asdict(value).items():
        if isinstance(item, float) and not isfinite(item):
            result[name] = None
        else:
            result[name] = item
    return result


class FrozenHoldoutSealRequest(BaseModel):
    experiment_id: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    dataset_content_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    holdout_start_at: datetime
    holdout_end_at: datetime
    feature_version: str = Field(min_length=1, max_length=120)
    strategy_name: str = Field(min_length=1, max_length=120)
    strategy_version: str = Field(min_length=1, max_length=120)
    model_version: str = Field(min_length=1, max_length=120)
    git_sha: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    parameters: dict[str, object] = Field(default_factory=dict)
    execution_assumptions: dict[str, object] = Field(default_factory=dict)

    @field_validator("experiment_id", "dataset_content_sha256", "git_sha")
    @classmethod
    def normalize_hex(cls, value: str) -> str:
        return value.lower()

    @model_validator(mode="after")
    def validate_window(self) -> FrozenHoldoutSealRequest:
        if not _aware(self.holdout_start_at) or not _aware(self.holdout_end_at):
            raise ValueError("holdout timestamps must be timezone-aware")
        if self.holdout_start_at >= self.holdout_end_at:
            raise ValueError("holdout_start_at must be before holdout_end_at")
        return self


class FrozenHoldoutPolicyRequest(BaseModel):
    min_samples: int = Field(default=250, gt=0)
    min_cumulative_return: float = Field(default=0.0, allow_inf_nan=False)
    min_sharpe: float = Field(default=0.25, allow_inf_nan=False)
    max_drawdown: float = Field(default=0.20, ge=0.0, le=1.0, allow_inf_nan=False)


class FrozenHoldoutEvaluationRequest(BaseModel):
    seal_id: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    dataset_content_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    holdout_start_at: datetime
    holdout_end_at: datetime
    feature_version: str = Field(min_length=1, max_length=120)
    strategy_name: str = Field(min_length=1, max_length=120)
    strategy_version: str = Field(min_length=1, max_length=120)
    model_version: str = Field(min_length=1, max_length=120)
    git_sha: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    parameters: dict[str, object] = Field(default_factory=dict)
    execution_assumptions: dict[str, object] = Field(default_factory=dict)
    returns: list[float] = Field(min_length=1)
    policy: FrozenHoldoutPolicyRequest = Field(default_factory=FrozenHoldoutPolicyRequest)

    @field_validator("seal_id", "dataset_content_sha256", "git_sha")
    @classmethod
    def normalize_hex(cls, value: str) -> str:
        return value.lower()

    @model_validator(mode="after")
    def validate_evidence(self) -> FrozenHoldoutEvaluationRequest:
        if not _aware(self.holdout_start_at) or not _aware(self.holdout_end_at):
            raise ValueError("holdout timestamps must be timezone-aware")
        if self.holdout_start_at >= self.holdout_end_at:
            raise ValueError("holdout_start_at must be before holdout_end_at")
        if any(not isfinite(value) or value <= -1.0 for value in self.returns):
            raise ValueError("holdout returns must be finite and greater than -1")
        return self


def _seal_from_request(request: FrozenHoldoutSealRequest) -> FrozenHoldoutSeal:
    return FrozenHoldoutSeal(
        experiment_id=request.experiment_id,
        dataset_content_sha256=request.dataset_content_sha256,
        holdout_start_at=request.holdout_start_at,
        holdout_end_at=request.holdout_end_at,
        feature_version=request.feature_version,
        strategy_name=request.strategy_name,
        strategy_version=request.strategy_version,
        model_version=request.model_version,
        git_sha=request.git_sha,
        parameters_fingerprint=_fingerprint(request.parameters),
        execution_assumptions_fingerprint=_fingerprint(request.execution_assumptions),
    )


def _evidence_from_request(request: FrozenHoldoutEvaluationRequest) -> FrozenHoldoutEvidence:
    return FrozenHoldoutEvidence(
        seal_id=request.seal_id,
        dataset_content_sha256=request.dataset_content_sha256,
        holdout_start_at=request.holdout_start_at,
        holdout_end_at=request.holdout_end_at,
        feature_version=request.feature_version,
        strategy_name=request.strategy_name,
        strategy_version=request.strategy_version,
        model_version=request.model_version,
        git_sha=request.git_sha,
        parameters_fingerprint=_fingerprint(request.parameters),
        execution_assumptions_fingerprint=_fingerprint(request.execution_assumptions),
        returns=tuple(request.returns),
    )


@router.post("/seal")
async def seal_holdout(request: FrozenHoldoutSealRequest) -> dict[str, object]:
    if persistence_engine is None:
        metrics.increment("validation_holdout_seal_rejected")
        raise HTTPException(
            status_code=503,
            detail="frozen holdout requires durable persistence",
        )
    try:
        seal = _seal_from_request(request)
        persisted = await persist_frozen_holdout_seal(persistence_engine, seal)
    except ValueError as error:
        metrics.increment("validation_holdout_seal_rejected")
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        metrics.increment("validation_holdout_seal_rejected")
        raise HTTPException(status_code=409, detail=str(error)) from error

    metrics.increment("validation_holdout_seals")
    return {
        "seal_id": seal.seal_id,
        "experiment_id": seal.experiment_id,
        "status": "SEALED",
        "persisted": persisted,
        "one_shot": True,
        "financial_connectivity": False,
        "real_money_execution": False,
    }


@router.post("/evaluate")
async def evaluate_holdout(request: FrozenHoldoutEvaluationRequest) -> dict[str, object]:
    if persistence_engine is None:
        metrics.increment("validation_holdout_evaluation_rejected")
        raise HTTPException(
            status_code=503,
            detail="frozen holdout requires durable persistence",
        )

    try:
        seal = await load_frozen_holdout_seal(persistence_engine, request.seal_id)
    except RuntimeError as error:
        metrics.increment("validation_holdout_evaluation_rejected")
        raise HTTPException(status_code=409, detail=str(error)) from error
    if seal is None:
        metrics.increment("validation_holdout_evaluation_rejected")
        raise HTTPException(status_code=404, detail="frozen holdout seal not found")

    try:
        evidence = _evidence_from_request(request)
        policy = FrozenHoldoutPolicy(**request.policy.model_dump())
        decision = evaluate_frozen_holdout(seal, evidence, policy=policy)
        consumption_id, persisted, idempotent_retry = await consume_frozen_holdout_decision(
            persistence_engine,
            decision,
        )
    except ValueError as error:
        metrics.increment("validation_holdout_evaluation_rejected")
        raise HTTPException(status_code=409, detail=str(error)) from error
    except RuntimeError as error:
        metrics.increment("validation_holdout_evaluation_rejected")
        raise HTTPException(status_code=409, detail=str(error)) from error

    metrics.increment("validation_holdout_evaluations")
    if decision.holdout_passed:
        metrics.increment("validation_holdout_passed")
    else:
        metrics.increment("validation_holdout_failed")
    return {
        "seal_id": decision.seal_id,
        "experiment_id": decision.experiment_id,
        "consumption_id": consumption_id,
        "status": decision.status,
        "holdout_passed": decision.holdout_passed,
        "failed_checks": list(decision.failed_checks),
        "checks": [asdict(item) for item in decision.checks],
        "metrics": _safe_metrics(decision.metrics),
        "evaluation_fingerprint": decision.evaluation_fingerprint,
        "persisted": persisted,
        "idempotent_retry": idempotent_retry,
        "one_shot": True,
        "paper_trading_only": decision.paper_trading_only,
        "live_execution_eligible": decision.live_execution_eligible,
        "financial_connectivity": decision.financial_connectivity,
        "real_money_execution": decision.real_money_execution,
    }
