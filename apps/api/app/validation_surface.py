from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from math import isfinite
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from services.validation import (
    ParameterPoint,
    PromotionGateEvidence,
    PromotionGatePolicy,
    deflated_sharpe_ratio,
    effective_number_of_trials,
    evaluate_promotion_gate,
    monte_carlo_block_bootstrap,
    parameter_stability,
    probability_of_backtest_overfitting,
    purged_walk_forward_splits,
    regime_robustness,
    validation_report,
)
from services.validation.experiments import stable_fingerprint

from .app_state import persistence_engine
from .metrics_state import metrics
from .research_persistence import (
    persist_model_promotion_decision,
    persist_research_experiment,
)

router = APIRouter(prefix="/research/validation", tags=["research", "validation"])


def _json_safe(value: object) -> object:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not isfinite(value):
        return None
    return value


def _unprocessable(error: ValueError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _trial_matrix(
    values: list[list[float]],
) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(item) for item in values)


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
    trial_returns: list[list[float]] | None = None
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
        if self.parameter_points is not None and len(self.parameter_points) < 3:
            raise ValueError("parameter_points requires at least three points")
        if self.trial_returns is not None:
            if not self.trial_returns:
                raise ValueError("trial_returns requires at least one trial")
            if any(len(values) != len(self.returns) for values in self.trial_returns):
                raise ValueError("trial_returns must have the same sample length as returns")
        return self


class EffectiveTrialsRequest(BaseModel):
    trial_returns: list[list[float]] = Field(min_length=1)


class PboRequest(BaseModel):
    strategy_returns: list[list[float]] = Field(min_length=2)
    segments: int = Field(default=8, ge=4)

    @model_validator(mode="after")
    def validate_matrix(self) -> PboRequest:
        if self.segments % 2 != 0:
            raise ValueError("segments must be even")
        lengths = {len(values) for values in self.strategy_returns}
        if len(lengths) != 1 or not lengths or 0 in lengths:
            raise ValueError("strategy return series must have equal non-zero length")
        sample_count = next(iter(lengths))
        if sample_count < self.segments or sample_count % self.segments != 0:
            raise ValueError("sample count must be divisible by segments")
        return self


class DatasetProvenanceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=200)
    venue: str = Field(min_length=1, max_length=80)
    data_level: Literal["L1", "L2", "L3", "TRADES", "MIXED"]
    content_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    schema_version: str = Field(min_length=1, max_length=64)
    symbols: list[str] = Field(min_length=1)
    start_at: datetime
    end_at: datetime
    event_count: int = Field(gt=0)
    quality: dict[str, object] = Field(default_factory=dict)

    @field_validator("content_sha256")
    @classmethod
    def normalize_content_sha256(cls, value: str) -> str:
        return value.lower()

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        symbols = sorted({item.strip().upper() for item in value if item.strip()})
        if not symbols:
            raise ValueError("symbols must contain at least one non-empty symbol")
        return symbols

    @model_validator(mode="after")
    def validate_time_bounds(self) -> DatasetProvenanceRequest:
        if not _aware(self.start_at) or not _aware(self.end_at):
            raise ValueError("dataset timestamps must be timezone-aware")
        if self.start_at >= self.end_at:
            raise ValueError("dataset start_at must be before end_at")
        return self


class ExperimentWindowRequest(BaseModel):
    role: Literal["TRAIN", "VALIDATION", "TEST", "OOS"]
    start_at: datetime
    end_at: datetime

    @model_validator(mode="after")
    def validate_window(self) -> ExperimentWindowRequest:
        if not _aware(self.start_at) or not _aware(self.end_at):
            raise ValueError("experiment window timestamps must be timezone-aware")
        if self.start_at >= self.end_at:
            raise ValueError("experiment window start_at must be before end_at")
        return self


class ExperimentManifestRequest(BaseModel):
    research_mode: Literal["HISTORICAL_REPLAY", "SIMULATION", "PAPER_TRADING"]
    dataset: DatasetProvenanceRequest
    feature_version: str = Field(min_length=1, max_length=120)
    strategy_name: str = Field(min_length=1, max_length=120)
    strategy_version: str = Field(min_length=1, max_length=120)
    model_version: str = Field(min_length=1, max_length=120)
    git_sha: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    seed: int
    replay_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    windows: list[ExperimentWindowRequest] = Field(min_length=1)
    parameters: dict[str, object] = Field(default_factory=dict)
    execution_assumptions: dict[str, object] = Field(default_factory=dict)

    @field_validator("git_sha")
    @classmethod
    def normalize_git_sha(cls, value: str) -> str:
        return value.lower()

    @field_validator("replay_fingerprint")
    @classmethod
    def normalize_replay_fingerprint(cls, value: str | None) -> str | None:
        return value.lower() if value is not None else None

    @model_validator(mode="after")
    def validate_windows_against_dataset(self) -> ExperimentManifestRequest:
        previous_end: datetime | None = None
        for window in self.windows:
            if window.start_at < self.dataset.start_at or window.end_at > self.dataset.end_at:
                raise ValueError("experiment windows must be contained by dataset coverage")
            if previous_end is not None and window.start_at < previous_end:
                raise ValueError("experiment windows must be ordered and non-overlapping")
            previous_end = window.end_at
        return self


class ExperimentValidationRequest(BaseModel):
    manifest: ExperimentManifestRequest
    validation: ValidationRequest


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
    delay_control_sharpe: float | None = Field(
        default=None,
        allow_inf_nan=False,
    )
    shuffle_control_sharpe: float | None = Field(
        default=None,
        allow_inf_nan=False,
    )

    @field_validator("experiment_id")
    @classmethod
    def normalize_experiment_id(cls, value: str) -> str:
        return value.lower()


class PromotionPolicyRequest(BaseModel):
    min_oos_samples: int = Field(default=250, gt=0)
    min_validation_folds: int = Field(default=5, gt=0)
    min_cumulative_return: float = Field(default=0.0, allow_inf_nan=False)
    min_sharpe: float = Field(default=0.25, allow_inf_nan=False)
    min_positive_fold_fraction: float = Field(
        default=0.60,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    min_robustness_score: float = Field(
        default=0.60,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    min_deflated_sharpe_ratio: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    max_probability_of_backtest_overfitting: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    max_drawdown: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    max_monte_carlo_probability_of_loss: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    min_regime_robustness_score: float = Field(
        default=0.60,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    min_parameter_stability_score: float = Field(
        default=0.60,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    max_negative_control_sharpe_ratio: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )


class PromotionEvaluationRequest(BaseModel):
    evidence: PromotionEvidenceRequest
    policy: PromotionPolicyRequest = Field(default_factory=PromotionPolicyRequest)


def _declared_trial_accounting(trials: int) -> dict[str, object]:
    return {
        "declared_trials": trials,
        "implied_independent_trials": float(trials),
        "effective_independent_trials": trials,
        "average_pairwise_correlation": None,
        "pair_count": None,
        "method": "declared_trials",
    }


def run_validation_report(request: ValidationRequest) -> dict[str, object]:
    returns = tuple(request.returns)
    try:
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
        trial_report = (
            effective_number_of_trials(_trial_matrix(request.trial_returns))
            if request.trial_returns is not None
            else None
        )
        dsr_trials = (
            trial_report.effective_independent_trials
            if trial_report is not None
            else request.trials
        )
        dsr = deflated_sharpe_ratio(returns, trials=dsr_trials)
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
    except ValueError as error:
        metrics.increment("validation_report_rejected")
        raise _unprocessable(error) from error

    metrics.increment("validation_report_requests")
    if trial_report is not None:
        metrics.increment("validation_effective_trial_evidence_used")
    return {
        "fold_count": len(folds),
        "performance": _json_safe(report.metrics),
        "positive_fold_fraction": report.positive_fold_fraction,
        "worst_fold_return": report.worst_fold_return,
        "median_fold_return": report.median_fold_return,
        "robustness_score": report.robustness_score,
        "deflated_sharpe_ratio": dsr,
        "dsr_trials": dsr_trials,
        "trial_accounting": (
            _json_safe(trial_report)
            if trial_report is not None
            else _declared_trial_accounting(request.trials)
        ),
        "monte_carlo": _json_safe(monte_carlo),
        "regime": _json_safe(regime_report) if regime_report is not None else None,
        "parameter_stability": (
            _json_safe(stability_report) if stability_report is not None else None
        ),
        "financial_connectivity": False,
        "real_money_execution": False,
    }


@router.post("/report")
def validation_report_endpoint(request: ValidationRequest) -> dict[str, object]:
    return run_validation_report(request)


@router.post("/trials/effective")
def effective_trials_endpoint(request: EffectiveTrialsRequest) -> dict[str, object]:
    try:
        report = effective_number_of_trials(_trial_matrix(request.trial_returns))
    except ValueError as error:
        metrics.increment("validation_effective_trials_rejected")
        raise _unprocessable(error) from error

    metrics.increment("validation_effective_trials_requests")
    return {
        "trial_accounting": _json_safe(report),
        "financial_connectivity": False,
        "real_money_execution": False,
    }


@router.post("/pbo")
def pbo_endpoint(request: PboRequest) -> dict[str, object]:
    try:
        pbo = probability_of_backtest_overfitting(
            tuple(tuple(values) for values in request.strategy_returns),
            segments=request.segments,
        )
    except ValueError as error:
        metrics.increment("validation_pbo_rejected")
        raise _unprocessable(error) from error

    metrics.increment("validation_pbo_requests")
    return {
        "probability_of_backtest_overfitting": pbo,
        "strategy_count": len(request.strategy_returns),
        "financial_connectivity": False,
        "real_money_execution": False,
    }


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
        raise _unprocessable(error) from error

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


@router.post("/experiments/validate")
async def validate_experiment(
    request: ExperimentValidationRequest,
) -> dict[str, object]:
    manifest = request.manifest.model_dump(mode="json")
    validation_plan = request.validation.model_dump(
        mode="json",
        exclude={"returns", "trial_returns"},
    )
    try:
        dataset_fingerprint = stable_fingerprint(manifest["dataset"])
        experiment_id = stable_fingerprint(
            {
                "manifest": manifest,
                "validation_plan": validation_plan,
            }
        )
        returns_fingerprint = stable_fingerprint({"returns": request.validation.returns})
        trial_family_fingerprint = (
            stable_fingerprint({"trial_returns": request.validation.trial_returns})
            if request.validation.trial_returns is not None
            else None
        )
    except ValueError as error:
        metrics.increment("research_experiment_rejected")
        raise _unprocessable(error) from error

    validation_result = run_validation_report(request.validation)
    payload: dict[str, object] = {
        "manifest": manifest,
        "validation_plan": validation_plan,
        "returns_fingerprint": returns_fingerprint,
        "trial_family_fingerprint": trial_family_fingerprint,
        "validation_result": validation_result,
    }
    try:
        persisted = await persist_research_experiment(
            persistence_engine,
            experiment_id=experiment_id,
            payload=payload,
        )
    except RuntimeError as error:
        metrics.increment("research_experiment_collision")
        raise HTTPException(status_code=409, detail=str(error)) from error

    metrics.increment("research_experiment_requests")
    if persisted:
        metrics.increment("research_experiment_persisted")
    return {
        "experiment_id": experiment_id,
        "dataset_fingerprint": dataset_fingerprint,
        "returns_fingerprint": returns_fingerprint,
        "trial_family_fingerprint": trial_family_fingerprint,
        "manifest": manifest,
        "validation_plan": validation_plan,
        "validation_result": validation_result,
        "persisted": persisted,
        "financial_connectivity": False,
        "real_money_execution": False,
    }
