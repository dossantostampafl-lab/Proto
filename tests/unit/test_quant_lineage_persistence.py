from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

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
async def test_quant_lineage_transaction_rolls_back_on_duplicate_correlation() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_database(engine)
        result = _result()
        assert await persist_quant_lineage(engine, result) is True

        with pytest.raises(Exception):
            await persist_quant_lineage(engine, result)

        async with engine.connect() as connection:
            table = CANONICAL_TABLES["model_predictions"]
            rows = (
                await connection.execute(
                    select(table).where(table.c.correlation_id == result.correlation_id)
                )
            ).mappings().all()
            assert len(rows) == 1
    finally:
        await engine.dispose()
