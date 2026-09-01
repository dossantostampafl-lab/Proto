from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.app.holdout_persistence import (
    consume_frozen_holdout_decision,
    frozen_holdout_consumption_id,
    load_frozen_holdout_seal,
    persist_frozen_holdout_seal,
)
from apps.api.app.schema_registry import canonical_metadata
from services.validation.holdout import (
    FrozenHoldoutEvidence,
    FrozenHoldoutSeal,
    evaluate_frozen_holdout,
)

HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
HEX_D = "d" * 64
START = datetime(2026, 2, 1, tzinfo=UTC)
END = START + timedelta(days=31)


def _seal() -> FrozenHoldoutSeal:
    return FrozenHoldoutSeal(
        experiment_id=HEX_A,
        dataset_content_sha256=HEX_B,
        holdout_start_at=START,
        holdout_end_at=END,
        feature_version="features-v1",
        strategy_name="microstructure-specialist",
        strategy_version="1.0.0",
        model_version="model-v1",
        git_sha="abc1234",
        parameters_fingerprint=HEX_C,
        execution_assumptions_fingerprint=HEX_D,
    )


def _decision(seal: FrozenHoldoutSeal, returns: tuple[float, ...]):
    evidence = FrozenHoldoutEvidence(
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
    return evaluate_frozen_holdout(seal, evidence)


@pytest.mark.asyncio
async def test_seal_round_trip_is_durable_and_idempotent() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(canonical_metadata.create_all)

    seal = _seal()
    assert await persist_frozen_holdout_seal(engine, seal) is True
    assert await persist_frozen_holdout_seal(engine, seal) is True
    assert await load_frozen_holdout_seal(engine, seal.seal_id) == seal

    await engine.dispose()


@pytest.mark.asyncio
async def test_consumption_is_one_shot_with_exact_retry_idempotency() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(canonical_metadata.create_all)

    seal = _seal()
    await persist_frozen_holdout_seal(engine, seal)
    decision = _decision(seal, (0.01, 0.005) * 125)

    consumption_id, persisted, idempotent_retry = await consume_frozen_holdout_decision(
        engine,
        decision,
    )
    assert consumption_id == frozen_holdout_consumption_id(seal.seal_id)
    assert persisted is True
    assert idempotent_retry is False

    retry_id, retry_persisted, retry_idempotent = await consume_frozen_holdout_decision(
        engine,
        decision,
    )
    assert retry_id == consumption_id
    assert retry_persisted is True
    assert retry_idempotent is True

    different_decision = _decision(seal, (0.009, 0.004) * 125)
    with pytest.raises(RuntimeError, match="already been consumed"):
        await consume_frozen_holdout_decision(engine, different_decision)

    await engine.dispose()
