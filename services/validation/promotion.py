from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Literal

from .experiments import stable_fingerprint

CandidateKind = Literal["CONTROL", "ALPHA_CANDIDATE"]
PromotionStatus = Literal[
    "CONTROL_ONLY",
    "RESEARCH_ONLY",
    "PAPER_TRADING_ELIGIBLE",
]


@dataclass(frozen=True, slots=True)
class PromotionGatePolicy:
    min_oos_samples: int = 250
    min_validation_folds: int = 5
    min_cumulative_return: float = 0.0
    min_sharpe: float = 0.25
    min_positive_fold_fraction: float = 0.60
    min_robustness_score: float = 0.60
    min_deflated_sharpe_ratio: float = 0.95
    max_probability_of_backtest_overfitting: float = 0.20
    max_drawdown: float = 0.20
    max_monte_carlo_probability_of_loss: float = 0.35
    min_regime_robustness_score: float = 0.60
    min_parameter_stability_score: float = 0.60
    max_negative_control_sharpe_ratio: float = 0.75

    def __post_init__(self) -> None:
        if self.min_oos_samples <= 0:
            raise ValueError("min_oos_samples must be positive")
        if self.min_validation_folds <= 0:
            raise ValueError("min_validation_folds must be positive")
        finite_values = (
            self.min_cumulative_return,
            self.min_sharpe,
            self.min_positive_fold_fraction,
            self.min_robustness_score,
            self.min_deflated_sharpe_ratio,
            self.max_probability_of_backtest_overfitting,
            self.max_drawdown,
            self.max_monte_carlo_probability_of_loss,
            self.min_regime_robustness_score,
            self.min_parameter_stability_score,
            self.max_negative_control_sharpe_ratio,
        )
        if any(not isfinite(value) for value in finite_values):
            raise ValueError("promotion policy thresholds must be finite")
        bounded = (
            self.min_positive_fold_fraction,
            self.min_robustness_score,
            self.min_deflated_sharpe_ratio,
            self.max_probability_of_backtest_overfitting,
            self.max_drawdown,
            self.max_monte_carlo_probability_of_loss,
            self.min_regime_robustness_score,
            self.min_parameter_stability_score,
            self.max_negative_control_sharpe_ratio,
        )
        if any(not 0.0 <= value <= 1.0 for value in bounded):
            raise ValueError("bounded promotion thresholds must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class PromotionGateEvidence:
    experiment_id: str
    candidate_kind: CandidateKind
    oos_sample_count: int
    validation_fold_count: int
    cumulative_return: float
    sharpe: float
    max_drawdown: float
    positive_fold_fraction: float
    robustness_score: float
    deflated_sharpe_ratio: float | None
    probability_of_backtest_overfitting: float | None
    monte_carlo_probability_of_loss: float | None
    regime_robustness_score: float | None
    parameter_stability_score: float | None
    delay_control_sharpe: float | None
    shuffle_control_sharpe: float | None

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id must not be blank")
        if self.oos_sample_count < 0:
            raise ValueError("oos_sample_count must be non-negative")
        if self.validation_fold_count < 0:
            raise ValueError("validation_fold_count must be non-negative")
        required_finite = (
            self.cumulative_return,
            self.sharpe,
            self.max_drawdown,
            self.positive_fold_fraction,
            self.robustness_score,
        )
        if any(not isfinite(value) for value in required_finite):
            raise ValueError("required promotion evidence must be finite")
        optional_finite = (
            self.deflated_sharpe_ratio,
            self.probability_of_backtest_overfitting,
            self.monte_carlo_probability_of_loss,
            self.regime_robustness_score,
            self.parameter_stability_score,
            self.delay_control_sharpe,
            self.shuffle_control_sharpe,
        )
        if any(value is not None and not isfinite(value) for value in optional_finite):
            raise ValueError("optional promotion evidence must be finite when present")
        if self.max_drawdown < 0.0:
            raise ValueError("max_drawdown must be non-negative")
        bounded = (
            self.positive_fold_fraction,
            self.robustness_score,
            self.deflated_sharpe_ratio,
            self.probability_of_backtest_overfitting,
            self.monte_carlo_probability_of_loss,
            self.regime_robustness_score,
            self.parameter_stability_score,
        )
        if any(
            value is not None and not 0.0 <= value <= 1.0
            for value in bounded
        ):
            raise ValueError("bounded promotion evidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class PromotionCheck:
    name: str
    passed: bool
    observed: int | float | str | None
    requirement: str


@dataclass(frozen=True, slots=True)
class PromotionGateDecision:
    experiment_id: str
    status: PromotionStatus
    checks: tuple[PromotionCheck, ...]
    failed_checks: tuple[str, ...]
    decision_fingerprint: str
    promotion_eligible: bool
    paper_trading_only: bool = True
    live_execution_eligible: bool = False
    financial_connectivity: bool = False
    real_money_execution: bool = False


def _check(
    name: str,
    passed: bool,
    observed: int | float | str | None,
    requirement: str,
) -> PromotionCheck:
    return PromotionCheck(
        name=name,
        passed=passed,
        observed=observed,
        requirement=requirement,
    )


def _minimum_check(
    name: str,
    observed: int | float | None,
    minimum: int | float,
) -> PromotionCheck:
    return _check(
        name,
        observed is not None and observed >= minimum,
        observed,
        f">= {minimum}",
    )


def _maximum_check(
    name: str,
    observed: float | None,
    maximum: float,
) -> PromotionCheck:
    return _check(
        name,
        observed is not None and observed <= maximum,
        observed,
        f"<= {maximum}",
    )


def _negative_control_check(
    name: str,
    observed: float | None,
    *,
    candidate_sharpe: float,
    max_ratio: float,
) -> PromotionCheck:
    threshold = candidate_sharpe * max_ratio
    return _check(
        name,
        observed is not None and candidate_sharpe > 0.0 and observed <= threshold,
        observed,
        f"<= candidate_sharpe * {max_ratio} ({threshold})",
    )


def _candidate_checks(
    evidence: PromotionGateEvidence,
    policy: PromotionGatePolicy,
) -> tuple[PromotionCheck, ...]:
    return (
        _minimum_check(
            "oos_sample_count",
            evidence.oos_sample_count,
            policy.min_oos_samples,
        ),
        _minimum_check(
            "validation_fold_count",
            evidence.validation_fold_count,
            policy.min_validation_folds,
        ),
        _minimum_check(
            "cumulative_return",
            evidence.cumulative_return,
            policy.min_cumulative_return,
        ),
        _minimum_check("sharpe", evidence.sharpe, policy.min_sharpe),
        _minimum_check(
            "positive_fold_fraction",
            evidence.positive_fold_fraction,
            policy.min_positive_fold_fraction,
        ),
        _minimum_check(
            "robustness_score",
            evidence.robustness_score,
            policy.min_robustness_score,
        ),
        _minimum_check(
            "deflated_sharpe_ratio",
            evidence.deflated_sharpe_ratio,
            policy.min_deflated_sharpe_ratio,
        ),
        _maximum_check(
            "probability_of_backtest_overfitting",
            evidence.probability_of_backtest_overfitting,
            policy.max_probability_of_backtest_overfitting,
        ),
        _maximum_check("max_drawdown", evidence.max_drawdown, policy.max_drawdown),
        _maximum_check(
            "monte_carlo_probability_of_loss",
            evidence.monte_carlo_probability_of_loss,
            policy.max_monte_carlo_probability_of_loss,
        ),
        _minimum_check(
            "regime_robustness_score",
            evidence.regime_robustness_score,
            policy.min_regime_robustness_score,
        ),
        _minimum_check(
            "parameter_stability_score",
            evidence.parameter_stability_score,
            policy.min_parameter_stability_score,
        ),
        _negative_control_check(
            "delay_control_sharpe",
            evidence.delay_control_sharpe,
            candidate_sharpe=evidence.sharpe,
            max_ratio=policy.max_negative_control_sharpe_ratio,
        ),
        _negative_control_check(
            "shuffle_control_sharpe",
            evidence.shuffle_control_sharpe,
            candidate_sharpe=evidence.sharpe,
            max_ratio=policy.max_negative_control_sharpe_ratio,
        ),
    )


def evaluate_promotion_gate(
    evidence: PromotionGateEvidence,
    *,
    policy: PromotionGatePolicy | None = None,
) -> PromotionGateDecision:
    """Fail closed when deciding whether alpha evidence may enter paper trading."""

    resolved_policy = policy or PromotionGatePolicy()
    if evidence.candidate_kind == "CONTROL":
        checks = (
            _check(
                "control_boundary",
                True,
                "CONTROL",
                "research controls are never promotion eligible",
            ),
        )
        status: PromotionStatus = "CONTROL_ONLY"
        promotion_eligible = False
    else:
        checks = _candidate_checks(evidence, resolved_policy)
        promotion_eligible = all(item.passed for item in checks)
        status = "PAPER_TRADING_ELIGIBLE" if promotion_eligible else "RESEARCH_ONLY"

    failed_checks = tuple(item.name for item in checks if not item.passed)
    decision_payload = {
        "evidence": asdict(evidence),
        "policy": asdict(resolved_policy),
        "checks": [asdict(item) for item in checks],
        "status": status,
        "promotion_eligible": promotion_eligible,
        "paper_trading_only": True,
        "live_execution_eligible": False,
    }
    return PromotionGateDecision(
        experiment_id=evidence.experiment_id,
        status=status,
        checks=checks,
        failed_checks=failed_checks,
        decision_fingerprint=stable_fingerprint(decision_payload),
        promotion_eligible=promotion_eligible,
    )
