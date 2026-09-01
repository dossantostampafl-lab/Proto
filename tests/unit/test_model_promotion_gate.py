from __future__ import annotations

from dataclasses import replace

import pytest

from services.validation import (
    PromotionGateEvidence,
    PromotionGatePolicy,
    evaluate_promotion_gate,
)


def _passing_evidence(**overrides: object) -> PromotionGateEvidence:
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
        "family_reality_check_p_value": 0.01,
        "family_spa_p_value": 0.01,
        "frozen_holdout_passed": True,
        "frozen_holdout_consumed": True,
        "frozen_holdout_seal_id": "holdout-seal-1",
    }
    values.update(overrides)
    return PromotionGateEvidence(**values)  # type: ignore[arg-type]


def test_candidate_must_pass_every_gate_for_paper_trading() -> None:
    decision = evaluate_promotion_gate(_passing_evidence())

    assert decision.status == "PAPER_TRADING_ELIGIBLE"
    assert decision.promotion_eligible is True
    assert decision.failed_checks == ()
    assert len(decision.checks) == 19
    assert all(item.passed for item in decision.checks)
    assert decision.paper_trading_only is True
    assert decision.live_execution_eligible is False
    assert decision.financial_connectivity is False
    assert decision.real_money_execution is False
    assert len(decision.decision_fingerprint) == 64


def test_control_can_never_be_promoted_even_with_strong_metrics() -> None:
    decision = evaluate_promotion_gate(_passing_evidence(candidate_kind="CONTROL"))

    assert decision.status == "CONTROL_ONLY"
    assert decision.promotion_eligible is False
    assert decision.failed_checks == ()
    assert decision.checks[0].name == "control_boundary"
    assert decision.checks[0].passed is True
    assert decision.live_execution_eligible is False


def test_missing_overfitting_evidence_fails_closed() -> None:
    decision = evaluate_promotion_gate(
        _passing_evidence(probability_of_backtest_overfitting=None)
    )

    assert decision.status == "RESEARCH_ONLY"
    assert "probability_of_backtest_overfitting" in decision.failed_checks


def test_missing_regime_and_parameter_evidence_fails_closed() -> None:
    decision = evaluate_promotion_gate(
        _passing_evidence(
            regime_robustness_score=None,
            parameter_stability_score=None,
        )
    )

    assert decision.status == "RESEARCH_ONLY"
    assert "regime_robustness_score" in decision.failed_checks
    assert "parameter_stability_score" in decision.failed_checks


def test_negative_controls_must_degrade_candidate_signal() -> None:
    decision = evaluate_promotion_gate(
        _passing_evidence(delay_control_sharpe=0.70, shuffle_control_sharpe=0.65)
    )

    assert decision.status == "RESEARCH_ONLY"
    assert "delay_control_sharpe" in decision.failed_checks
    assert "shuffle_control_sharpe" in decision.failed_checks


def test_family_level_data_snooping_evidence_is_mandatory() -> None:
    missing = evaluate_promotion_gate(
        _passing_evidence(
            family_reality_check_p_value=None,
            family_spa_p_value=None,
        )
    )
    weak = evaluate_promotion_gate(
        _passing_evidence(
            family_reality_check_p_value=0.20,
            family_spa_p_value=0.10,
        )
    )

    assert missing.status == "RESEARCH_ONLY"
    assert "family_reality_check_p_value" in missing.failed_checks
    assert "family_spa_p_value" in missing.failed_checks
    assert weak.status == "RESEARCH_ONLY"
    assert "family_reality_check_p_value" in weak.failed_checks
    assert "family_spa_p_value" in weak.failed_checks


def test_frozen_holdout_must_be_passed_consumed_and_referenced() -> None:
    decision = evaluate_promotion_gate(
        _passing_evidence(
            frozen_holdout_passed=False,
            frozen_holdout_consumed=False,
            frozen_holdout_seal_id=None,
        )
    )

    assert decision.status == "RESEARCH_ONLY"
    assert "frozen_holdout_passed" in decision.failed_checks
    assert "frozen_holdout_consumed" in decision.failed_checks
    assert "frozen_holdout_seal_id" in decision.failed_checks


def test_oos_and_drawdown_thresholds_are_fail_closed() -> None:
    decision = evaluate_promotion_gate(
        _passing_evidence(oos_sample_count=249, max_drawdown=0.21)
    )

    assert decision.status == "RESEARCH_ONLY"
    assert "oos_sample_count" in decision.failed_checks
    assert "max_drawdown" in decision.failed_checks


def test_policy_is_explicit_and_changes_decision_fingerprint() -> None:
    evidence = _passing_evidence()
    default_decision = evaluate_promotion_gate(evidence)
    relaxed = PromotionGatePolicy(
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
        max_family_reality_check_p_value=0.10,
        max_family_spa_p_value=0.10,
    )

    relaxed_decision = evaluate_promotion_gate(evidence, policy=relaxed)

    assert relaxed_decision.status == "PAPER_TRADING_ELIGIBLE"
    assert default_decision.decision_fingerprint != relaxed_decision.decision_fingerprint


def test_decision_fingerprint_is_deterministic() -> None:
    evidence = _passing_evidence()
    first = evaluate_promotion_gate(evidence)
    second = evaluate_promotion_gate(evidence)

    assert first == second
    assert first.decision_fingerprint == second.decision_fingerprint


def test_invalid_evidence_is_rejected_before_gate_evaluation() -> None:
    with pytest.raises(ValueError, match="candidate_kind"):
        _passing_evidence(candidate_kind="UNKNOWN")
    with pytest.raises(ValueError, match="required promotion evidence must be finite"):
        _passing_evidence(sharpe=float("nan"))
    with pytest.raises(ValueError, match="bounded promotion evidence"):
        _passing_evidence(family_spa_p_value=1.1)
    with pytest.raises(ValueError, match="frozen_holdout_seal_id"):
        _passing_evidence(frozen_holdout_seal_id="")


def test_invalid_policy_is_rejected() -> None:
    with pytest.raises(ValueError, match="min_oos_samples"):
        PromotionGatePolicy(min_oos_samples=0)
    with pytest.raises(ValueError, match="bounded promotion thresholds"):
        PromotionGatePolicy(max_family_spa_p_value=1.1)


def test_candidate_with_nonpositive_sharpe_cannot_pass_negative_controls() -> None:
    evidence = replace(
        _passing_evidence(),
        sharpe=0.0,
        delay_control_sharpe=-1.0,
        shuffle_control_sharpe=-1.0,
    )
    decision = evaluate_promotion_gate(evidence)

    assert decision.status == "RESEARCH_ONLY"
    assert "sharpe" in decision.failed_checks
    assert "delay_control_sharpe" in decision.failed_checks
    assert "shuffle_control_sharpe" in decision.failed_checks
