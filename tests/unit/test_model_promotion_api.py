from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.api.app.validation_surface import (
    PromotionEvaluationRequest,
    PromotionEvidenceRequest,
    PromotionPolicyRequest,
    promotion_evaluate_endpoint,
)


def _evidence(**overrides: object) -> PromotionEvidenceRequest:
    values: dict[str, object] = {
        "experiment_id": "a" * 64,
        "candidate_kind": "ALPHA_CANDIDATE",
        "oos_sample_count": 500,
        "validation_fold_count": 8,
        "cumulative_return": 0.12,
        "sharpe": 0.80,
        "max_drawdown": 0.08,
        "positive_fold_fraction": 0.75,
        "robustness_score": 0.82,
        "deflated_sharpe_ratio": 0.98,
        "probability_of_backtest_overfitting": 0.10,
        "monte_carlo_probability_of_loss": 0.20,
        "regime_robustness_score": 0.75,
        "parameter_stability_score": 0.70,
        "delay_control_sharpe": 0.20,
        "shuffle_control_sharpe": 0.10,
    }
    values.update(overrides)
    return PromotionEvidenceRequest(**values)  # type: ignore[arg-type]


def test_api_promotes_only_to_paper_trading_eligibility() -> None:
    request = PromotionEvaluationRequest(evidence=_evidence())

    result = promotion_evaluate_endpoint(request)

    assert result["status"] == "PAPER_TRADING_ELIGIBLE"
    assert result["promotion_eligible"] is True
    assert result["failed_checks"] == []
    assert len(result["checks"]) == 14
    assert len(result["decision_fingerprint"]) == 64
    assert result["paper_trading_only"] is True
    assert result["live_execution_eligible"] is False
    assert result["financial_connectivity"] is False
    assert result["real_money_execution"] is False


def test_api_keeps_controls_research_only() -> None:
    request = PromotionEvaluationRequest(
        evidence=_evidence(candidate_kind="CONTROL")
    )

    result = promotion_evaluate_endpoint(request)

    assert result["status"] == "CONTROL_ONLY"
    assert result["promotion_eligible"] is False
    assert result["live_execution_eligible"] is False
    assert result["checks"] == [
        {
            "name": "control_boundary",
            "passed": True,
            "observed": "CONTROL",
            "requirement": "research controls are never promotion eligible",
        }
    ]


def test_api_missing_statistical_evidence_fails_closed() -> None:
    request = PromotionEvaluationRequest(
        evidence=_evidence(
            probability_of_backtest_overfitting=None,
            regime_robustness_score=None,
            parameter_stability_score=None,
        )
    )

    result = promotion_evaluate_endpoint(request)

    assert result["status"] == "RESEARCH_ONLY"
    assert result["promotion_eligible"] is False
    assert "probability_of_backtest_overfitting" in result["failed_checks"]
    assert "regime_robustness_score" in result["failed_checks"]
    assert "parameter_stability_score" in result["failed_checks"]


def test_api_policy_is_explicit_in_response_and_fingerprint() -> None:
    evidence = _evidence()
    default_result = promotion_evaluate_endpoint(
        PromotionEvaluationRequest(evidence=evidence)
    )
    relaxed_policy = PromotionPolicyRequest(
        min_oos_samples=100,
        min_validation_folds=3,
        min_cumulative_return=-0.01,
        min_sharpe=0.10,
        min_positive_fold_fraction=0.50,
        min_robustness_score=0.50,
        min_deflated_sharpe_ratio=0.80,
        max_probability_of_backtest_overfitting=0.30,
        max_drawdown=0.30,
        max_monte_carlo_probability_of_loss=0.50,
        min_regime_robustness_score=0.50,
        min_parameter_stability_score=0.50,
        max_negative_control_sharpe_ratio=0.90,
    )
    relaxed_result = promotion_evaluate_endpoint(
        PromotionEvaluationRequest(evidence=evidence, policy=relaxed_policy)
    )

    assert relaxed_result["policy"] == relaxed_policy.model_dump(mode="json")
    assert default_result["decision_fingerprint"] != relaxed_result[
        "decision_fingerprint"
    ]


def test_api_request_schema_rejects_invalid_candidate_kind() -> None:
    with pytest.raises(ValidationError):
        _evidence(candidate_kind="UNKNOWN")


def test_api_request_schema_rejects_non_finite_and_unbounded_values() -> None:
    with pytest.raises(ValidationError):
        _evidence(sharpe=float("nan"))

    with pytest.raises(ValidationError):
        _evidence(positive_fold_fraction=1.1)

    with pytest.raises(ValidationError):
        PromotionPolicyRequest(max_drawdown=1.1)


def test_api_decision_is_deterministic() -> None:
    request = PromotionEvaluationRequest(evidence=_evidence())

    first = promotion_evaluate_endpoint(request)
    second = promotion_evaluate_endpoint(request)

    assert first == second
