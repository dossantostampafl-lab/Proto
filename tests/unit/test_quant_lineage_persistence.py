from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from apps.api.app.persistence import build_engine, init_database
from apps.api.app.research_persistence import persist_quant_lineage
from apps.api.app.schema_registry import CANONICAL_TABLES
from services.quant.pipeline import CalibrationSample, QuantPipelineInput, run_quant_pipeline


def _result():
    observed_at = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    return run_quant_pipeline(
        QuantPipelineInput(
            market_id="btc-lineage",
            symbol="BTC",
            observed_at=observed_at,
            market_probability=0.51,
            volatility=0.20,
            imbalance=0.2,
            liquidity_score=0.9,
            fees=0.0,
            slippage=0.0,
            spread_cost=0.0,
            hedge_cost=0.0,
            latency_penalty=0.0,
            calibration_samples=(
                CalibrationSample(probability=0.50, outcome=0),
                CalibrationSample(probability=0.55, outcome=1),
            ),
            event_times=(observed_at.timestamp() - 1.0,),
            expiry_at=observed_at + timedelta(hours=1),
        ),
        correlation_id="quant-lineage-test",
    )


@pytest.mark.asyncio
async def test_disabled_quant_lineage_persistence_is_explicit() -> None:
    assert await persist_quant_lineage(None, _result()) is False


@pytest.mark.asyncio
async def test_quant_lineage_persists_all_required_research_records() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_database(engine)
        result = _result()

        assert await persist_quant_lineage(engine, result) is True

        expected_tables = {
            "model_predictions",
            "fair_values",
            "edges",
            "model_metrics",
            "calibration_metrics",
            "hawkes_states",
            "audit_events",
        }
        async with engine.connect() as connection:
            for table_name in expected_tables:
                table = CANONICAL_TABLES[table_name]
                rows = (
                    await connection.execute(
                        select(table).where(table.c.correlation_id == result.correlation_id)
                    )
                ).mappings().all()
                assert len(rows) == 1, table_name
                assert rows[0]["payload"]["market_id"] == "btc-lineage"
                assert rows[0]["payload"]["symbol"] == "BTC"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_quant_lineage_duplicate_correlation_is_idempotent() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_database(engine)
        result = _result()
        assert await persist_quant_lineage(engine, result) is True
        assert await persist_quant_lineage(engine, result) is True

        async with engine.connect() as connection:
            for table_name in (
                "model_predictions",
                "fair_values",
                "edges",
                "model_metrics",
                "calibration_metrics",
                "hawkes_states",
                "audit_events",
            ):
                table = CANONICAL_TABLES[table_name]
                rows = (
                    await connection.execute(
                        select(table).where(table.c.correlation_id == result.correlation_id)
                    )
                ).mappings().all()
                assert len(rows) == 1, table_name
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_quant_lineage_partial_collision_still_rolls_back_atomically() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_database(engine)
        result = _result()
        prediction_table = CANONICAL_TABLES["model_predictions"]
        async with engine.begin() as connection:
            await connection.execute(
                insert(prediction_table).values(
                    id="quant-lineage-test:prediction",
                    created_at=datetime.now(UTC),
                    correlation_id=result.correlation_id,
                    payload={"event": "partial-conflict"},
                )
            )

        with pytest.raises(IntegrityError):
            await persist_quant_lineage(engine, result)

        async with engine.connect() as connection:
            prediction_rows = (
                await connection.execute(
                    select(prediction_table).where(
                        prediction_table.c.correlation_id == result.correlation_id
                    )
                )
            ).mappings().all()
            assert len(prediction_rows) == 1
            assert prediction_rows[0]["payload"] == {"event": "partial-conflict"}

            for table_name in (
                "fair_values",
                "edges",
                "model_metrics",
                "calibration_metrics",
                "hawkes_states",
                "audit_events",
            ):
                table = CANONICAL_TABLES[table_name]
                rows = (
                    await connection.execute(
                        select(table).where(table.c.correlation_id == result.correlation_id
                    )
                ).mappings().all()
                assert rows == [], table_name
    finally:
        await engine.dispose()
