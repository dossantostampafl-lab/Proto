from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Literal

from .core import PerformanceMetrics, performance_metrics
from .experiments import stable_fingerprint

HoldoutStatus = Literal["PASSED", "FAILED"]


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _is_hex(value: str, *, minimum: int, maximum: int) -> bool:
    if not minimum <= len(value) <= maximum:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class FrozenHoldoutSeal:
    experiment_id: str
    dataset_content_sha256: str
    holdout_start_at: datetime
    holdout_end_at: datetime
    feature_version: str
    strategy_name: str
    strategy_version: str
    model_version: str
    git_sha: str
    parameters_fingerprint: str
    execution_assumptions_fingerprint: str

    def __post_init__(self) -> None:
        if not _is_hex(self.experiment_id, minimum=64, maximum=64):
            raise ValueError("experiment_id must be a 64-character hex fingerprint")
        for name, value in (
            ("dataset_content_sha256", self.dataset_content_sha256),
            ("parameters_fingerprint", self.parameters_fingerprint),
            ("execution_assumptions_fingerprint", self.execution_assumptions_fingerprint),
        ):
            if not _is_hex(value, minimum=64, maximum=64):
                raise ValueError(f"{name} must be a 64-character hex fingerprint")
        if not _is_hex(self.git_sha, minimum=7, maximum=64):
            raise ValueError("git_sha must be a 7-64 character hex commit identifier")
        if not _is_aware(self.holdout_start_at) or not _is_aware(self.holdout_end_at):
            raise ValueError("holdout timestamps must be timezone-aware")
        if self.holdout_start_at >= self.holdout_end_at:
            raise ValueError("holdout_start_at must be before holdout_end_at")
        for name, value in (
            ("feature_version", self.feature_version),
            ("strategy_name", self.strategy_name),
            ("strategy_version", self.strategy_version),
            ("model_version", self.model_version),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")

    def immutable_context(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id.lower(),
            "dataset_content_sha256": self.dataset_content_sha256.lower(),
            "holdout_start_at": self.holdout_start_at.astimezone(UTC),
            "holdout_end_at": self.holdout_end_at.astimezone(UTC),
            "feature_version": self.feature_version,
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "model_version": self.model_version,
            "git_sha": self.git_sha.lower(),
            "parameters_fingerprint": self.parameters_fingerprint.lower(),
            "execution_assumptions_fingerprint": self.execution_assumptions_fingerprint.lower(),
        }

    def persistence_payload(self) -> dict[str, object]:
        context = self.immutable_context()
        return {
            **context,
            "holdout_start_at": self.holdout_start_at.astimezone(UTC).isoformat(),
            "holdout_end_at": self.holdout_end_at.astimezone(UTC).isoformat(),
        }

    @property
    def seal_id(self) -> str:
        return stable_fingerprint(
            {"record_type": "frozen_holdout_seal", "context": self.immutable_context()}
        )

    @classmethod
    def from_persistence_payload(cls, payload: dict[str, object]) -> FrozenHoldoutSeal:
        return cls(
            experiment_id=str(payload["experiment_id"]),
            dataset_content_sha256=str(payload["dataset_content_sha256"]),
            holdout_start_at=datetime.fromisoformat(str(payload["holdout_start_at"])),
            holdout_end_at=datetime.fromisoformat(str(payload["holdout_end_at"])),
            feature_version=str(payload["feature_version"]),
            strategy_name=str(payload["strategy_name"]),
            strategy_version=str(payload["strategy_version"]),
            model_version=str(payload["model_version"]),
            git_sha=str(payload["git_sha"]),
            parameters_fingerprint=str(payload["parameters_fingerprint"]),
            execution_assumptions_fingerprint=str(
                payload["execution_assumptions_fingerprint"]
            ),
        )


@dataclass(frozen=True, slots=True)
class FrozenHoldoutPolicy:
    min_samples: int = 250
    min_cumulative_return: float = 0.0
    min_sharpe: float = 0.25
    max_drawdown: float = 0.20

    def __post_init__(self) -> None:
        if self.min_samples <= 0:
            raise ValueError("min_samples must be positive")
        values = (self.min_cumulative_return, self.min_sharpe, self.max_drawdown)
        if any(not isfinite(value) for value in values):
            raise ValueError("holdout policy thresholds must be finite")
        if not 0.0 <= self.max_drawdown <= 1.0:
            raise ValueError("max_drawdown must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class FrozenHoldoutEvidence:
    seal_id: str
    dataset_content_sha256: str
    holdout_start_at: datetime
    holdout_end_at: datetime
    feature_version: str
    strategy_name: str
    strategy_version: str
    model_version: str
    git_sha: str
    parameters_fingerprint: str
    execution_assumptions_fingerprint: str
    returns: tuple[float, ...]

    def __post_init__(self) -> None:
        if not _is_hex(self.seal_id, minimum=64, maximum=64):
            raise ValueError("seal_id must be a 64-character hex fingerprint")
        if not self.returns:
            raise ValueError("holdout returns must not be empty")
        if any(not isfinite(value) or value <= -1.0 for value in self.returns):
            raise ValueError("holdout returns must be finite and greater than -1")

    def context(self) -> dict[str, object]:
        return {
            "dataset_content_sha256": self.dataset_content_sha256.lower(),
            "holdout_start_at": self.holdout_start_at.astimezone(UTC),
            "holdout_end_at": self.holdout_end_at.astimezone(UTC),
            "feature_version": self.feature_version,
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "model_version": self.model_version,
            "git_sha": self.git_sha.lower(),
            "parameters_fingerprint": self.parameters_fingerprint.lower(),
            "execution_assumptions_fingerprint": self.execution_assumptions_fingerprint.lower(),
        }


@dataclass(frozen=True, slots=True)
class FrozenHoldoutCheck:
    name: str
    passed: bool
    observed: int | float
    requirement: str


@dataclass(frozen=True, slots=True)
class FrozenHoldoutDecision:
    seal_id: str
    experiment_id: str
    status: HoldoutStatus
    metrics: PerformanceMetrics
    checks: tuple[FrozenHoldoutCheck, ...]
    failed_checks: tuple[str, ...]
    evaluation_fingerprint: str
    holdout_passed: bool
    paper_trading_only: bool = True
    live_execution_eligible: bool = False
    financial_connectivity: bool = False
    real_money_execution: bool = False


def _context_mismatches(
    seal: FrozenHoldoutSeal,
    evidence: FrozenHoldoutEvidence,
) -> tuple[str, ...]:
    expected = seal.immutable_context().copy()
    expected.pop("experiment_id")
    observed = evidence.context()
    return tuple(name for name, value in expected.items() if observed.get(name) != value)


def evaluate_frozen_holdout(
    seal: FrozenHoldoutSeal,
    evidence: FrozenHoldoutEvidence,
    *,
    policy: FrozenHoldoutPolicy | None = None,
) -> FrozenHoldoutDecision:
    """Evaluate a previously sealed holdout without allowing context drift."""

    if evidence.seal_id != seal.seal_id:
        raise ValueError("holdout evidence seal_id does not match the frozen seal")
    mismatches = _context_mismatches(seal, evidence)
    if mismatches:
        raise ValueError(
            "holdout evidence does not match frozen seal: " + ", ".join(mismatches)
        )

    resolved_policy = policy or FrozenHoldoutPolicy()
    metrics = performance_metrics(evidence.returns)
    checks = (
        FrozenHoldoutCheck(
            name="sample_count",
            passed=metrics.sample_count >= resolved_policy.min_samples,
            observed=metrics.sample_count,
            requirement=f">= {resolved_policy.min_samples}",
        ),
        FrozenHoldoutCheck(
            name="cumulative_return",
            passed=metrics.cumulative_return >= resolved_policy.min_cumulative_return,
            observed=metrics.cumulative_return,
            requirement=f">= {resolved_policy.min_cumulative_return}",
        ),
        FrozenHoldoutCheck(
            name="sharpe",
            passed=metrics.sharpe >= resolved_policy.min_sharpe,
            observed=metrics.sharpe,
            requirement=f">= {resolved_policy.min_sharpe}",
        ),
        FrozenHoldoutCheck(
            name="max_drawdown",
            passed=metrics.max_drawdown <= resolved_policy.max_drawdown,
            observed=metrics.max_drawdown,
            requirement=f"<= {resolved_policy.max_drawdown}",
        ),
    )
    passed = all(item.passed for item in checks)
    status: HoldoutStatus = "PASSED" if passed else "FAILED"
    failed_checks = tuple(item.name for item in checks if not item.passed)
    fingerprint_payload = {
        "seal_id": seal.seal_id,
        "policy": asdict(resolved_policy),
        "metrics": {
            "sample_count": metrics.sample_count,
            "cumulative_return": metrics.cumulative_return,
            "sharpe": metrics.sharpe,
            "max_drawdown": metrics.max_drawdown,
        },
        "status": status,
    }
    return FrozenHoldoutDecision(
        seal_id=seal.seal_id,
        experiment_id=seal.experiment_id,
        status=status,
        metrics=metrics,
        checks=checks,
        failed_checks=failed_checks,
        evaluation_fingerprint=stable_fingerprint(fingerprint_payload),
        holdout_passed=passed,
    )
