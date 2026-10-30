from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, select, update
from sqlalchemy.ext.asyncio import create_async_engine

from services.quant.evidence import (
    EvidenceCollision,
    QuantEvidence,
    quant_evidence_metadata,
    quant_evidence_table,
    recent_quant_evidence,
    store_quant_evidence,
)
from services.quant.pipeline import QuantPipelineInput, run_quant_pipeline


def envelope(**updates):
    observed = updates.get("observed_at", datetime(2026, 10, 29, 12, tzinfo=UTC))
    cost = {"value": "0.001", "source": "fixture", "observed_at": observed.isoformat()}
    pipeline = QuantPipelineInput(
        market_id="fixture",
        symbol="BTC",
        observed_at=observed,
        market_probability=0.5,
        volatility=0.2,
        imbalance=0,
        liquidity_score=0.9,
        fees=0.001,
        slippage=0.001,
        spread_cost=0.001,
        hedge_cost=0.001,
        latency_penalty=0.001,
        calibration_samples=({"probability": 0.5, "outcome": 1},),
    )
    computed = run_quant_pipeline(pipeline)
    data = {
        "instrument_id": "BINANCE_SPOT:BTCUSDT",
        "mode": "HISTORICAL_REPLAY",
        "observed_at": observed,
        "computed_at": "2026-10-29T14:00:00Z",
        "model_version": computed.model_version,
        "feature_version": computed.feature_version,
        "input_source": "fixture",
        "input_provenance": "REPLAY",
        "pipeline_input": pipeline,
        "costs": {
            name: cost
            for name in ("fees", "slippage", "spread_cost", "hedge_cost", "latency_penalty")
        },
        "calibration_samples": [
            {
                "probability": "0.5",
                "outcome": 1,
                "source": "fixture",
                "provenance": "REPLAY",
                "observed_at": (observed - timedelta(seconds=1)).isoformat(),
            }
        ],
        "result": {
            "raw_probability": str(computed.raw_probability),
            "fair_probability": str(computed.fair_probability),
            "confidence": str(computed.confidence),
            "uncertainty": str(computed.uncertainty),
            "net_edge": str(computed.edge.net_edge),
            "risk_adjusted_ev": str(computed.expected_value.risk_adjusted_ev),
        },
    }
    data.update(updates)
    return QuantEvidence.model_validate(data)


@pytest.mark.asyncio
async def test_evidence_is_durable_idempotent_and_read_only():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(quant_evidence_metadata.create_all)
        first = await store_quant_evidence(engine, envelope())
        assert first == await store_quant_evidence(engine, envelope())
        async with engine.connect() as conn:
            before = await conn.run_sync(lambda c: inspect(c).get_table_names())
            rows = (await conn.execute(select(quant_evidence_table))).all()
        found = await recent_quant_evidence(engine, instrument_id="BINANCE_SPOT:BTCUSDT")
        assert len(found) == len(rows) == 1
        assert found[0]["evidence_id"] == first
        assert found[0]["result"]["fair_probability"] == envelope().result["fair_probability"]
        assert await recent_quant_evidence(engine, instrument_id="OTHER:BTC") == []
        async with engine.connect() as conn:
            assert await conn.run_sync(lambda c: inspect(c).get_table_names()) == before
            assert len((await conn.execute(select(quant_evidence_table))).all()) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_collision_never_overwrites_persisted_evidence():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(quant_evidence_metadata.create_all)
        identity = await store_quant_evidence(engine, envelope())
        async with engine.begin() as conn:
            await conn.execute(
                update(quant_evidence_table)
                .where(quant_evidence_table.c.id == identity)
                .values(payload={"tampered": True})
            )
        with pytest.raises(EvidenceCollision):
            await store_quant_evidence(engine, envelope())
        with pytest.raises(EvidenceCollision):
            await recent_quant_evidence(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_disabled_storage_is_never_reported_as_success():
    with pytest.raises(LookupError):
        await store_quant_evidence(None, envelope())
    with pytest.raises(LookupError):
        await recent_quant_evidence(None)


@pytest.mark.asyncio
async def test_fabricated_result_is_not_persisted():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(quant_evidence_metadata.create_all)
        with pytest.raises(ValueError, match="pipeline result"):
            await store_quant_evidence(engine, envelope(result={"fair_probability": "0.1"}))
    finally:
        await engine.dispose()


def test_future_or_contemporaneous_labels_and_missing_cost_provenance_rejected():
    data = envelope().model_dump(mode="json")
    data["calibration_samples"][0]["observed_at"] = data["observed_at"]
    with pytest.raises(ValidationError):
        QuantEvidence.model_validate(data)


@pytest.mark.asyncio
async def test_recent_evidence_is_sorted_bounded_and_filtered_in_database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(quant_evidence_metadata.create_all)
        early = envelope()
        late = envelope(observed_at=datetime(2026, 10, 29, 13, tzinfo=UTC))
        other = envelope(instrument_id="OTHER:BTC")
        early_id = await store_quant_evidence(engine, early)
        late_id = await store_quant_evidence(engine, late)
        await store_quant_evidence(engine, other)
        found = await recent_quant_evidence(engine, limit=1, instrument_id=early.instrument_id)
        assert [row["evidence_id"] for row in found] == [late_id]
        found = await recent_quant_evidence(engine, limit=2, instrument_id=early.instrument_id)
        assert [row["evidence_id"] for row in found] == [late_id, early_id]
        for limit in (0, 101, True):
            with pytest.raises(ValueError):
                await recent_quant_evidence(engine, limit=limit)
    finally:
        await engine.dispose()


def test_nonfinite_values_and_future_cost_are_rejected():
    data = envelope().model_dump(mode="json")
    data["pipeline_input"]["volatility"] = "NaN"
    with pytest.raises(ValidationError):
        QuantEvidence.model_validate(data)
    data = envelope().model_dump(mode="json")
    data["costs"]["fees"]["observed_at"] = "2026-10-30T12:00:00Z"
    with pytest.raises(ValidationError):
        QuantEvidence.model_validate(data)
    data = envelope().model_dump(mode="json")
    data["costs"]["fees"]["source"] = ""
    with pytest.raises(ValidationError):
        QuantEvidence.model_validate(data)
