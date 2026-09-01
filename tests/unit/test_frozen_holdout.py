from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from services.validation.experiments import stable_fingerprint
from services.validation.holdout import (
    FrozenHoldoutEvidence,
    FrozenHoldoutPolicy,
    FrozenHoldoutSeal,
    evaluate_frozen_holdout,
)

HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
HEX_D = "d" * 64
START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(days=30)


def _seal() -> FrozenHoldoutSeal:
    return FrozenHoldoutSeal(
        experiment_id=HEX_A,
        dataset_content_sha256=HEX_B,
        holdout_start_at=START,
        holdout_end_at=END,
        feature_version="features-v1",
        strategy_name="trend-specialist",
        strategy_version="1.0.0",
        model_version="model-v1",
        git_sha="abc1234",
        parameters_fingerprint=HEX_C,
        execution_assumptions_fingerprint=HEX_D,
    )


def _evidence(
    seal: FrozenHoldoutSeal,
    returns: tuple[float, ...],
) -> FrozenHoldoutEvidence:
    return FrozenHoldoutEvidence(
        seal_id=seal.seal_id,
        dataset_content_sha256=seal.dataset_content_sha256,
        holdout_start_at=seal.holdout_start_at,
        holdout_end_at=seal.holdout_end_at,
        feature_version=seal.feature_version,
        strategy_name=seal.strategy_name,
        strategy_version=seal.strategy_version,
        model_version=seal.model_version,
        git_sha=seal.git_sha,
        parameters_fingerprint=seal.parameters_fingerprint,
        execution_assumptions_fingerprint=seal.execution_assumptions_fingerprint,
        returns=returns,
    )


def test_seal_id_is_deterministic_and_context_sensitive() -> None:
    seal = _seal()

    assert seal.seal_id == _seal().seal_id
    assert len(seal.seal_id) == 64
    assert seal.seal_id != replace(seal, git_sha="def5678").seal_id
    assert stable_fingerprint(seal.persistence_payload()) == stable_fingerprint(
        _seal().persistence_payload()
    )


def test_strong_frozen_holdout_passes_without_live_eligibility() -> None:
    seal = _seal()
    returns = (0.01, 0.005) * 125

    decision = evaluate_frozen_holdout(seal, _evidence(seal, returns))

    assert decision.status == "PASSED"
    assert decision.holdout_passed is True
    assert decision.failed_checks == ()
    assert decision.metrics.sample_count == 250
    assert decision.paper_trading_only is True
    assert decision.live_execution_eligible is False
    assert decision.financial_connectivity is False
    assert decision.real_money_execution is False


def test_weak_holdout_fails_and_names_failed_checks() -> None:
    seal = _seal()
    returns = (-0.01, 0.0) * 125

    decision = evaluate_frozen_holdout(seal, _evidence(seal, returns))

    assert decision.status == "FAILED"
    assert decision.holdout_passed is False
    assert "cumulative_return" in decision.failed_checks
    assert "sharpe" in decision.failed_checks


def test_context_drift_is_rejected_before_scoring() -> None:
    seal = _seal()
    evidence = replace(_evidence(seal, (0.01, 0.005) * 125), git_sha="def5678")

    with pytest.raises(ValueError, match="git_sha"):
        evaluate_frozen_holdout(seal, evidence)


def test_seal_id_mismatch_is_rejected() -> None:
    seal = _seal()
    evidence = replace(_evidence(seal, (0.01, 0.005) * 125), seal_id=HEX_D)

    with pytest.raises(ValueError, match="seal_id"):
        evaluate_frozen_holdout(seal, evidence)


def test_policy_can_fail_closed_on_sample_count() -> None:
    seal = _seal()
    evidence = _evidence(seal, (0.01, 0.005) * 50)
    policy = FrozenHoldoutPolicy(min_samples=250)

    decision = evaluate_frozen_holdout(seal, evidence, policy=policy)

    assert decision.status == "FAILED"
    assert "sample_count" in decision.failed_checks
