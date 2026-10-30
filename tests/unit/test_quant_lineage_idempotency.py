from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.app.research_persistence import persist_quant_lineage
from apps.api.app.schema_registry import CANONICAL_TABLES, canonical_metadata
from services.quant.pipeline import CalibrationSample, QuantPipelineInput, run_quant_pipeline


@pytest.mark.asyncio
async def test_quant_lineage_retry_is_idempotent_and_complete() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(canonical_metadata.create_all)

    result = run_quant_pipeline(
        QuantPipelineInput(
            market_id="btc-idempotent-replay",
            symbol="BTC",
            observed_at=datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
            market_probability=0.52,
            volatility=0.25,
            imbalance=0.10,
            liquidity_score=1.0,
            fees=0.0,
            slippage=0.0,
            spread_cost=0.0,
            hedge_cost=0.0,
            latency_penalty=0.0,
            calibration_samples=(CalibrationSample(probability=0.5, outcome=1),),
        )
    )

    assert await persist_quant_lineage(engine, result) is True
    assert await persist_quant_lineage(engine, result) is True

    expected_tables = (
        "model_predictions",
        "fair_values",
        "edges",
        "model_metrics",
        "hawkes_states",
        "calibration_metrics",
        "audit_events",
    )
    async with engine.connect() as connection:
        for table_name in expected_tables:
            table = CANONICAL_TABLES[table_name]
            count = await connection.scalar(select(func.count()).select_from(table))
            assert count == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_quant_lineage_rejects_reused_identity_with_different_results() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(canonical_metadata.create_all)
        data = QuantPipelineInput(
            market_id="btc", symbol="BTC", observed_at=datetime(2026, 8, 31, tzinfo=UTC),
            market_probability=0.5, volatility=0.2, imbalance=0.1, liquidity_score=0.9,
            fees=0.0, slippage=0.0, spread_cost=0.0, hedge_cost=0.0, latency_penalty=0.0,
        )
        first = run_quant_pipeline(data, correlation_id="reused")
        different = run_quant_pipeline(
            data.model_copy(update={"volatility": 0.4}), correlation_id="reused"
        )
        assert await persist_quant_lineage(engine, first)
        with pytest.raises(RuntimeError, match="lineage identity collision"):
            await persist_quant_lineage(engine, different)
        async with engine.connect() as connection:
            table = CANONICAL_TABLES["model_predictions"]
            stored = (await connection.execute(select(table.c.payload))).scalar_one()
            assert stored["raw_probability"] == first.raw_probability
    finally:
        await engine.dispose()
