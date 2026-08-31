from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from services.quant.pipeline import QuantPipelineResult

from .schema_registry import CANONICAL_TABLES


def _record_id(correlation_id: str, suffix: str) -> str:
    value = f"{correlation_id}:{suffix}"
    if len(value) <= 64:
        return value
    return f"{correlation_id[:48]}:{suffix[:15]}"


async def _lineage_complete(
    engine: AsyncEngine,
    correlation_id: str,
    rows: list[tuple[str, str, dict[str, object]]],
) -> bool:
    async with engine.connect() as connection:
        for table_name, suffix, _ in rows:
            table = CANONICAL_TABLES[table_name]
            record_id = _record_id(correlation_id, suffix)
            existing = await connection.execute(
                select(table.c.id).where(table.c.id == record_id)
            )
            if existing.scalar_one_or_none() is None:
                return False
    return True


async def persist_quant_lineage(
    engine: AsyncEngine | None,
    result: QuantPipelineResult,
) -> bool:
    """Persist one complete research lineage atomically when storage is enabled.

    Deterministic correlation IDs make replay retries idempotent. A concurrent or
    repeated write that collides with an already-complete lineage is treated as a
    successful idempotent replay; partial or unrelated integrity failures surface.
    """
    if engine is None:
        return False

    created_at = datetime.now(UTC)
    correlation_id = result.correlation_id
    common = {
        "market_id": result.market_id,
        "symbol": result.symbol,
        "observed_at": result.observed_at.isoformat(),
        "model_version": result.model_version,
        "feature_version": result.feature_version,
    }

    rows: list[tuple[str, str, dict[str, object]]] = [
        (
            "model_predictions",
            "prediction",
            {
                **common,
                "raw_probability": result.raw_probability,
                "calibrated_probability": result.calibrated_probability,
                "confidence": result.confidence,
                "uncertainty": result.uncertainty,
            },
        ),
        (
            "fair_values",
            "fair-value",
            {
                **common,
                "fair_probability": result.fair_probability,
            },
        ),
        (
            "edges",
            "edge",
            {
                **common,
                "edge": result.edge.model_dump(mode="json"),
                "expected_value": result.expected_value.model_dump(mode="json"),
            },
        ),
        (
            "model_metrics",
            "model-metrics",
            {
                **common,
                "confidence": result.confidence,
                "uncertainty": result.uncertainty,
                "time_exposure": result.time_exposure.model_dump(mode="json"),
                "synthetic_greeks": {
                    "market_probability_delta": result.greeks.market_probability_delta,
                    "volatility_vega": result.greeks.volatility_vega,
                    "imbalance_kappa": result.greeks.imbalance_kappa,
                    "time_theta": result.greeks.time_theta,
                    "bump_size": result.greeks.bump_size,
                },
            },
        ),
        (
            "hawkes_states",
            "hawkes",
            {
                **common,
                "baseline_intensity": result.hawkes.baseline_intensity,
                "current_intensity": result.hawkes.current_intensity,
                "excitation": result.hawkes.excitation,
                "decay": result.hawkes.decay,
                "branching_ratio": result.hawkes.branching_ratio,
                "event_probability": result.hawkes.event_probability,
            },
        ),
    ]

    if result.calibration_report is not None:
        rows.append(
            (
                "calibration_metrics",
                "calibration",
                {
                    **common,
                    "count": result.calibration_report.count,
                    "brier_score": result.calibration_report.brier_score,
                    "log_loss": result.calibration_report.log_loss,
                    "expected_calibration_error": (
                        result.calibration_report.expected_calibration_error
                    ),
                    "maximum_calibration_error": (
                        result.calibration_report.maximum_calibration_error
                    ),
                    "bins": [
                        {
                            "lower": item.lower,
                            "upper": item.upper,
                            "count": item.count,
                            "mean_probability": item.mean_probability,
                            "observed_frequency": item.observed_frequency,
                            "calibration_error": item.calibration_error,
                        }
                        for item in result.calibration_report.bins
                    ],
                },
            )
        )

    rows.append(
        (
            "audit_events",
            "quant-lineage",
            {
                **common,
                "event": "quant_pipeline_completed",
                "fair_probability": result.fair_probability,
                "net_edge": result.edge.net_edge,
                "decision": result.edge.decision,
            },
        )
    )

    try:
        async with engine.begin() as connection:
            for table_name, suffix, payload in rows:
                table = CANONICAL_TABLES[table_name]
                await connection.execute(
                    insert(table).values(
                        id=_record_id(correlation_id, suffix),
                        created_at=created_at,
                        correlation_id=correlation_id,
                        payload=payload,
                    )
                )
    except IntegrityError:
        if await _lineage_complete(engine, correlation_id, rows):
            return True
        raise
    return True


async def persist_research_experiment(
    engine: AsyncEngine | None,
    *,
    experiment_id: str,
    payload: dict[str, object],
) -> bool:
    """Append one deterministic experiment result when persistence is enabled.

    Replaying the exact same manifest/result is idempotent. Reusing the same
    experiment identity for a different result is rejected so research evidence
    cannot be silently overwritten.
    """
    if engine is None:
        return False

    table = CANONICAL_TABLES["research_experiments"]
    try:
        async with engine.begin() as connection:
            await connection.execute(
                insert(table).values(
                    id=experiment_id,
                    created_at=datetime.now(UTC),
                    correlation_id=experiment_id,
                    payload=payload,
                )
            )
    except IntegrityError as error:
        async with engine.connect() as connection:
            existing = await connection.execute(
                select(table.c.payload).where(table.c.id == experiment_id)
            )
            existing_payload = existing.scalar_one_or_none()
        if existing_payload is None:
            raise
        if existing_payload == payload:
            return True
        raise RuntimeError(
            "experiment identity collision: persisted payload differs"
        ) from error
    return True
