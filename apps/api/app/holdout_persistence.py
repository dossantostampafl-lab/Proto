from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from services.validation.experiments import stable_fingerprint
from services.validation.holdout import FrozenHoldoutDecision, FrozenHoldoutSeal

from .schema_registry import CANONICAL_TABLES


async def persist_frozen_holdout_seal(
    engine: AsyncEngine | None,
    seal: FrozenHoldoutSeal,
) -> bool:
    if engine is None:
        return False

    payload = {
        "record_type": "frozen_holdout_seal",
        "seal": seal.persistence_payload(),
    }
    table = CANONICAL_TABLES["research_experiments"]
    try:
        async with engine.begin() as connection:
            await connection.execute(
                insert(table).values(
                    id=seal.seal_id,
                    created_at=datetime.now(UTC),
                    correlation_id=seal.experiment_id,
                    payload=payload,
                )
            )
    except IntegrityError as error:
        async with engine.connect() as connection:
            existing = await connection.execute(
                select(table.c.payload).where(table.c.id == seal.seal_id)
            )
            existing_payload = existing.scalar_one_or_none()
        if existing_payload == payload:
            return True
        raise RuntimeError("frozen holdout seal identity collision") from error
    return True


async def load_frozen_holdout_seal(
    engine: AsyncEngine | None,
    seal_id: str,
) -> FrozenHoldoutSeal | None:
    if engine is None:
        return None

    table = CANONICAL_TABLES["research_experiments"]
    async with engine.connect() as connection:
        result = await connection.execute(
            select(table.c.payload).where(table.c.id == seal_id)
        )
        payload = result.scalar_one_or_none()
    if payload is None:
        return None
    if payload.get("record_type") != "frozen_holdout_seal":
        raise RuntimeError("frozen holdout seal record type mismatch")
    seal_payload = payload.get("seal")
    if not isinstance(seal_payload, dict):
        raise RuntimeError("frozen holdout seal payload is malformed")
    seal = FrozenHoldoutSeal.from_persistence_payload(seal_payload)
    if seal.seal_id != seal_id:
        raise RuntimeError("frozen holdout seal fingerprint mismatch")
    return seal


def frozen_holdout_consumption_id(seal_id: str) -> str:
    return stable_fingerprint(
        {"record_type": "frozen_holdout_consumption", "seal_id": seal_id}
    )


async def consume_frozen_holdout_decision(
    engine: AsyncEngine | None,
    decision: FrozenHoldoutDecision,
) -> tuple[str, bool, bool]:
    consumption_id = frozen_holdout_consumption_id(decision.seal_id)
    if engine is None:
        return consumption_id, False, False

    payload = {
        "record_type": "frozen_holdout_consumption",
        "seal_id": decision.seal_id,
        "experiment_id": decision.experiment_id,
        "status": decision.status,
        "failed_checks": list(decision.failed_checks),
        "evaluation_fingerprint": decision.evaluation_fingerprint,
        "checks": [asdict(item) for item in decision.checks],
        "metrics": {
            "sample_count": decision.metrics.sample_count,
            "cumulative_return": decision.metrics.cumulative_return,
            "sharpe": decision.metrics.sharpe,
            "max_drawdown": decision.metrics.max_drawdown,
        },
        "paper_trading_only": decision.paper_trading_only,
        "live_execution_eligible": decision.live_execution_eligible,
        "financial_connectivity": decision.financial_connectivity,
        "real_money_execution": decision.real_money_execution,
    }
    table = CANONICAL_TABLES["research_experiments"]
    try:
        async with engine.begin() as connection:
            await connection.execute(
                insert(table).values(
                    id=consumption_id,
                    created_at=datetime.now(UTC),
                    correlation_id=decision.experiment_id,
                    payload=payload,
                )
            )
    except IntegrityError as error:
        async with engine.connect() as connection:
            existing = await connection.execute(
                select(table.c.payload).where(table.c.id == consumption_id)
            )
            existing_payload = existing.scalar_one_or_none()
        if existing_payload == payload:
            return consumption_id, True, True
        raise RuntimeError(
            "frozen holdout has already been consumed by different evidence"
        ) from error
    return consumption_id, True, False
