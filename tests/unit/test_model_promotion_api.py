from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from apps.api.app import promotion_guard_surface
from apps.api.app.promotion_guard_surface import (
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
        "family_reality_check_p_value": 0.01,
        "family_spa_p_value": 0.01,
        "frozen_holdout_passed": True,
        "frozen_holdout_consumed": True,
        "frozen_holdout_seal_id": "holdout-seal-1",
    }
    values.update(overrides)
    return PromotionEvidenceRequest(**values)  # type: ignore[arg-type]


def _evaluate(request: PromotionEvaluationRequest) -> dict[str, object]:
    return asyncio.run(promotion_evaluate_endpoint(request))


def test_api_promotes_only_to_paper_trading_eligibility() -> None:
    result = _evaluate(PromotionEvaluationRequest(evidence=_evidence()))

    assert result["status"] == "PAPER_TRADING_ELIGIBLE"
    assert result["promotion_eligible"] is True
    assert result["failed_checks"] == []
    assert len(result["checks"]) == 19
    assert len(result["decision_fingerprint"]) == 64
    assert len(result["promotion_record_id"]) == 64
    assert isinstance(result["persisted"], bool)
    assert result["paper_trading_only"] is True
    assert result["live_execution_eligible"] is False
    assert result["financial_connectivity"] is False
    assert result["real_money_execution"] is False


def test_api_keeps_controls_research_only() -> None:
    result = _evaluate(
        PromotionEvaluationRequest(evidence=_evidence(candidate_kind="CONTROL"))
    )

    assert result["status"] == "CONTROL_ONLY"
    assert result["promotion_eligible"] is False
    assert result["live_execution_eligible"] is False


def test_api_missing_family_or_holdout_evidence_fails_closed() -> None:
    result = _evaluate(
        PromotionEvaluationRequest(
            evidence=_evidence(
                family_reality_check_p_value=None,
                family_spa_p_value=None,
                frozen_holdout_passed=False,
                frozen_holdout_consumed=False,
                frozen_holdout_seal_id=None,
            )
        )
    )

    assert result["status"] == "RESEARCH_ONLY"
    for name in (
        "family_reality_check_p_value",
        "family_spa_p_value",
        "frozen_holdout_passed",
        "frozen_holdout_consumed",
        "frozen_holdout_seal_id",
    ):
        assert name in result["failed_checks"]


def test_api_policy_is_explicit_in_response_and_fingerprint() -> None:
    evidence = _evidence()
    default_result = _evaluate(PromotionEvaluationRequest(evidence=evidence))
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
        max_family_reality_check_p_value=0.10,
        max_family_spa_p_value=0.10,
    )
    relaxed_result = _evaluate(
        PromotionEvaluationRequest(evidence=evidence, policy=relaxed_policy)
    )

    assert relaxed_result["policy"] == relaxed_policy.model_dump(mode="json")
    assert default_result["decision_fingerprint"] != relaxed_result[
        "decision_fingerprint"
    ]
    assert default_result["promotion_record_id"] != relaxed_result[
        "promotion_record_id"
    ]


def test_api_request_schema_rejects_invalid_evidence() -> None:
    with pytest.raises(ValidationError):
        _evidence(candidate_kind="UNKNOWN")
    with pytest.raises(ValidationError):
        _evidence(sharpe=float("nan"))
    with pytest.raises(ValidationError):
        _evidence(family_spa_p_value=1.1)
    with pytest.raises(ValidationError):
        _evidence(frozen_holdout_seal_id="")


def test_api_requires_canonical_experiment_fingerprint() -> None:
    with pytest.raises(ValidationError):
        _evidence(experiment_id="not-a-fingerprint")

    evidence = _evidence(experiment_id="A" * 64)
    assert evidence.experiment_id == "a" * 64


def test_api_decision_is_deterministic() -> None:
    request = PromotionEvaluationRequest(evidence=_evidence())

    first = _evaluate(request)
    second = _evaluate(request)

    assert first == second


def test_api_persists_complete_promotion_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_persist(
        engine: object,
        *,
        experiment_id: str,
        decision_fingerprint: str,
        payload: dict[str, object],
    ) -> tuple[str, bool]:
        captured.update(
            {
                "engine": engine,
                "experiment_id": experiment_id,
                "decision_fingerprint": decision_fingerprint,
                "payload": payload,
            }
        )
        return "f" * 64, True

    monkeypatch.setattr(
        promotion_guard_surface,
        "persist_model_promotion_decision",
        fake_persist,
    )

    result = _evaluate(PromotionEvaluationRequest(evidence=_evidence()))

    assert result["promotion_record_id"] == "f" * 64
    assert result["persisted"] is True
    payload = captured["payload"]
    assert isinstance(payload, dict)
    evidence = payload["evidence"]
    assert isinstance(evidence, dict)
    assert evidence["family_reality_check_p_value"] == 0.01
    assert evidence["family_spa_p_value"] == 0.01
    assert evidence["frozen_holdout_passed"] is True
    assert evidence["frozen_holdout_consumed"] is True
    assert evidence["frozen_holdout_seal_id"] == "holdout-seal-1"
    decision = payload["decision"]
    assert isinstance(decision, dict)
    assert decision["status"] == "PAPER_TRADING_ELIGIBLE"
    assert decision["live_execution_eligible"] is False


def test_api_fails_closed_on_promotion_identity_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_persist(*args: object, **kwargs: object) -> tuple[str, bool]:
        raise RuntimeError("promotion identity collision: persisted payload differs")

    monkeypatch.setattr(
        promotion_guard_surface,
        "persist_model_promotion_decision",
        fake_persist,
    )

    with pytest.raises(HTTPException) as error:
        _evaluate(PromotionEvaluationRequest(evidence=_evidence()))

    assert error.value.status_code == 409
