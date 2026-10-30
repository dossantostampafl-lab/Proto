"""Durable ProtoBrain quant producer: accepts evidence, never manufactures inputs."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from services.orchestration import JOB_CATALOG, JobState, ProtoBrain, SqlJobStore
from services.quant.evidence import (
    QuantEvidence,
    quant_evidence_metadata,
    recent_quant_evidence,
)
from services.quant.job import enqueue_quant_research, process_quant_research_job
from services.quant.pipeline import QuantPipelineInput, run_quant_pipeline


def envelope(*, mode="SIMULATION", provenance="SIMULATED", observed=None):
    observed = observed or datetime.now(UTC) - timedelta(seconds=30)
    pipeline = QuantPipelineInput(
        market_id="offline-test",
        symbol="BTC",
        observed_at=observed,
        market_probability=0.5,
        volatility=0.2,
        imbalance=0.0,
        liquidity_score=0.9,
        fees=0.001,
        slippage=0.001,
        spread_cost=0.001,
        hedge_cost=0.001,
        latency_penalty=0.001,
    )
    result = run_quant_pipeline(pipeline)
    return QuantEvidence(
        instrument_id="BINANCE_SPOT:BTCUSDT",
        mode=mode,
        observed_at=observed,
        computed_at=observed + timedelta(seconds=1),
        input_source="offline-test",
        input_provenance=provenance,
        pipeline_input=pipeline,
        model_version=result.model_version,
        feature_version=result.feature_version,
        costs={
            name: {
                "value": "0.001",
                "source": "offline-test",
                "observed_at": observed,
            }
            for name in ("fees", "slippage", "spread_cost", "hedge_cost", "latency_penalty")
        },
        result={
            "raw_probability": str(result.raw_probability),
            "fair_probability": str(result.fair_probability),
            "confidence": str(result.confidence),
            "uncertainty": str(result.uncertainty),
            "net_edge": str(result.edge.net_edge),
            "risk_adjusted_ev": str(result.expected_value.risk_adjusted_ev),
        },
    )


async def brain_with_storage(*, available=True):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    store = SqlJobStore(engine)
    await store.init_schema()
    async with engine.begin() as connection:
        await connection.run_sync(quant_evidence_metadata.create_all)
    brain = ProtoBrain(store=store, worker_id="offline-quant-worker")

    async def handle(payload, run):
        return await process_quant_research_job(payload, run, engine if available else None)

    brain.register_contextual(JOB_CATALOG["quant-research"].spec, handle)
    return brain, engine


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "provenance"),
    [
        ("SIMULATION", "SIMULATED"),
        ("SHADOW", "PAPER"),
        ("HISTORICAL_REPLAY", "REPLAY"),
    ],
)
async def test_quant_job_persists_exact_envelope_in_existing_queue(mode, provenance):
    brain, engine = await brain_with_storage()
    try:
        item = envelope(mode=mode, provenance=provenance)
        payload = {"evidence": item.model_dump(mode="json")}
        queued = await brain.enqueue(
            "quant-research", idempotency_key=f"quant:{mode}", mode=mode, payload=payload
        )
        completed = await brain.run_once()
        assert completed is not None
        assert completed.id == queued.id
        assert completed.state is JobState.SUCCEEDED
        assert completed.result["persisted"] is True
        assert completed.result["mode"] == mode
        assert completed.result["real_money_execution"] is False
        rows = await recent_quant_evidence(engine)
        assert len(rows) == 1
        assert rows[0]["evidence_id"] == completed.result["evidence_id"]
        assert rows[0]["input_provenance"] == provenance
        duplicate = await brain.enqueue(
            "quant-research", idempotency_key=f"quant:{mode}", mode=mode, payload=payload
        )
        assert duplicate.id == queued.id
        assert await brain.run_once() is None
        assert len(await recent_quant_evidence(engine)) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "provenance"),
    [
        ("SIMULATION", "PAPER"),
        ("SHADOW", "SIMULATED"),
        ("HISTORICAL_REPLAY", "PAPER"),
    ],
)
async def test_quant_job_rejects_false_provenance_without_persisting(mode, provenance):
    brain, engine = await brain_with_storage()
    try:
        item = envelope(mode=mode, provenance=provenance)
        await brain.enqueue(
            "quant-research",
            idempotency_key=f"wrong:{mode}",
            mode=mode,
            payload={"evidence": item.model_dump(mode="json")},
        )
        result = await brain.run_once()
        assert result is not None and result.state is JobState.RETRY_WAIT
        assert "provenance" in result.last_error
        assert await recent_quant_evidence(engine) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_quant_job_rejects_payload_mode_spoofing_and_invalid_result():
    brain, engine = await brain_with_storage()
    try:
        item = envelope(mode="SHADOW", provenance="PAPER")
        await brain.enqueue(
            "quant-research",
            idempotency_key="mode-spoof",
            mode="SIMULATION",
            payload={"evidence": item.model_dump(mode="json")},
        )
        failed = await brain.run_once()
        assert failed.state is JobState.RETRY_WAIT
        assert "mode differs" in failed.last_error
        assert await recent_quant_evidence(engine) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_quant_job_does_not_report_success_when_store_is_missing():
    brain, engine = await brain_with_storage(available=False)
    try:
        await brain.enqueue(
            "quant-research",
            idempotency_key="storage-missing",
            mode="SIMULATION",
            payload={"evidence": envelope().model_dump(mode="json")},
        )
        result = await brain.run_once()
        assert result.state is JobState.RETRY_WAIT
        assert "storage unavailable" in result.last_error
        assert result.result is None
        assert await recent_quant_evidence(engine) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_quant_job_rejects_future_observation_and_extra_payload_fields():
    brain, engine = await brain_with_storage()
    try:
        future = datetime.now(UTC) + timedelta(hours=1)
        await brain.enqueue(
            "quant-research",
            idempotency_key="future",
            mode="SIMULATION",
            payload={"evidence": envelope(observed=future).model_dump(mode="json")},
        )
        result = await brain.run_once()
        assert result.state is JobState.RETRY_WAIT
        assert "future" in result.last_error
        assert await recent_quant_evidence(engine) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_evidence_only_enqueuer_is_content_idempotent():
    brain, engine = await brain_with_storage()
    try:
        item = envelope()
        queued = await enqueue_quant_research(brain, item)
        replay = await enqueue_quant_research(brain, item)
        assert queued.id == replay.id
        assert queued.idempotency_key.startswith("quant-evidence:")
        assert queued.mode == "SIMULATION"
        result = await brain.run_once()
        assert result.state is JobState.SUCCEEDED
        assert len(await recent_quant_evidence(engine)) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_evidence_only_enqueuer_fails_closed_on_mislabeled_data():
    brain, engine = await brain_with_storage()
    try:
        with pytest.raises(ValueError, match="mode/provenance mismatch"):
            await enqueue_quant_research(brain, envelope(provenance="PAPER"))
        assert await brain.run_once() is None
        assert await recent_quant_evidence(engine) == []
    finally:
        await engine.dispose()
