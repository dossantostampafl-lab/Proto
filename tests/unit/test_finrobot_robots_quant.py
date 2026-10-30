"""Contract tests for the three evidence-bound FinRobots (offline, no venue calls)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

pytest.importorskip("services.finrobots.contracts", reason="requires architecture PR #360")

from services.finrobots import CycleContext, FinRobotId, Stance  # noqa: E402
from services.finrobots.robots import default_robots  # noqa: E402
from services.quant.evidence import QuantEvidence, _payload  # noqa: E402
from services.quant.pipeline import QuantPipelineInput, run_quant_pipeline  # noqa: E402

INSTRUMENT = "BINANCE_SPOT:BTCUSDT"


def _row(*, observed=None, provenance="PAPER", mode="SHADOW"):
    now = datetime.now(UTC)
    observed = observed or now - timedelta(minutes=1)
    costs = {name: 0.1 for name in (
        "fees", "slippage", "spread_cost", "hedge_cost", "latency_penalty"
    )}
    inputs = QuantPipelineInput(
        market_id="fixture",
        symbol="BTC",
        observed_at=observed,
        market_probability=0.5,
        volatility=0.2,
        imbalance=0,
        liquidity_score=0.9,
        **costs,
    )
    result = run_quant_pipeline(inputs)
    envelope = QuantEvidence(
        instrument_id=INSTRUMENT,
        mode=mode,
        observed_at=observed,
        computed_at=observed + timedelta(seconds=1),
        input_source="offline-fixture",
        input_provenance=provenance,
        pipeline_input=inputs,
        model_version=result.model_version,
        feature_version=result.feature_version,
        costs={
            name: {"value": str(value), "source": "offline-fixture", "observed_at": observed}
            for name, value in costs.items()
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
    identity, payload = _payload(envelope)
    return {"evidence_id": identity, **payload}


def _context(row, *, read_at=None, provenance=None, status="OK", instruments=(INSTRUMENT,)):
    now = datetime.now(UTC)
    return CycleContext(
        cycle_id=uuid4(), started_at=now, instrument_ids=instruments,
        snapshot={
            "quant.evidence": {"contract_version": "quant-evidence/1", "records": [row]},
            "_sources": {"sources": {"quant.evidence": {
                "status": status,
                "provenance": provenance or row["input_provenance"],
                "read_at": read_at or now.isoformat(),
            }}},
        },
    )


def _quant_robots():
    ids = {
        FinRobotId.QUANT_RESEARCH,
        FinRobotId.STRATEGY_RESEARCH,
        FinRobotId.MODEL_VALIDATOR,
    }
    return {r.robot_id: r for r in default_robots() if r.robot_id in ids}


@pytest.mark.asyncio
async def test_all_eight_robots_bound_and_quant_not_authorized_to_trade():
    robots = default_robots()
    assert len(robots) == len(set(r.robot_id for r in robots)) == 8
    context = _context(_row())
    result = {id_: (await robot.analyze(context))[0] for id_, robot in _quant_robots().items()}
    assert result[FinRobotId.QUANT_RESEARCH].stance is Stance.CAUTION
    assert result[FinRobotId.STRATEGY_RESEARCH].stance is Stance.ABSTAIN
    assert result[FinRobotId.MODEL_VALIDATOR].stance is Stance.ABSTAIN
    assert {r.cycle_id for r in result.values()} == {context.cycle_id}
    assert all(r.instrument_id == INSTRUMENT for r in result.values())
    assert all(
        not r.financial_connectivity and not r.real_money_execution
        for r in result.values()
    )
    expected_id = context.snapshot["quant.evidence"]["records"][0]["evidence_id"]
    assert all(r.evidence[0].metrics["evidence_id"] == expected_id for r in result.values())


@pytest.mark.asyncio
async def test_hash_tamper_never_produces_a_vote_or_cited_evidence():
    row = _row()
    row["result"] = {**row["result"], "net_edge": "0.99"}
    context = _context(row)
    for robot in _quant_robots().values():
        report = await robot.analyze(context)
        assert report.stance is Stance.ABSTAIN
        assert not report.evidence


@pytest.mark.asyncio
async def test_old_source_unknown_provenance_and_unlisted_instrument_abstain():
    row = _row()
    contexts = [
        _context(row, status="UNAVAILABLE"),
        _context(row, read_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat()),
        _context(row, provenance="LIVE"),
        _context(row, instruments=("OTHER:BTC",)),
    ]
    for context in contexts:
        for robot in _quant_robots().values():
            report = await robot.analyze(context)
            assert report.stance is Stance.ABSTAIN
            assert not report.evidence


@pytest.mark.asyncio
async def test_old_replay_never_casts_a_current_trading_caution():
    row = _row(
        observed=datetime.now(UTC) - timedelta(days=3),
        provenance="REPLAY", mode="HISTORICAL_REPLAY",
    )
    context = _context(row)
    quant = _quant_robots()[FinRobotId.QUANT_RESEARCH]
    report = (await quant.analyze(context))[0]
    assert report.stance is Stance.ABSTAIN
    assert report.evidence[0].provenance.value == "REPLAY"
