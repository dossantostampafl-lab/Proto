"""Persistent risk-permit gate for the Phase 4 OMS (test-first)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from services.execution import OrderType, ReleaseStage, TradeIntent, VenueId
from services.risk.durable_permits import (
    DurablePermitLedger,
    consumed_permits,
    permit_metadata,
)
from services.risk.permits import PermitRejected, PermitSigner

NOW = datetime(2026, 10, 13, 16, 0, tzinfo=UTC)
KEY = b"d" * 32


def trade(*, stage: ReleaseStage = ReleaseStage.PAPER) -> TradeIntent:
    return TradeIntent(
        intent_id=uuid4(),
        instrument_id="BINANCE_SPOT:BTCUSDT",
        venue=VenueId.BINANCE_SPOT,
        side="BUY",
        quantity=Decimal("0.001"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("62000"),
        strategy_id="hybrid-fusion-v1",
        model_version="fusion-v1",
        signal_ids=(uuid4(),),
        stage=stage,
        created_at=NOW,
    )


@pytest.fixture
async def engine(tmp_path):
    database = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'permits.db'}")
    async with database.begin() as conn:
        await conn.run_sync(permit_metadata.create_all)
    yield database
    await database.dispose()


async def test_consumption_survives_new_ledger_instance(engine) -> None:
    signer = PermitSigner(KEY)
    first = DurablePermitLedger(engine, signer)
    order = trade()
    permit = signer.issue(order, now=NOW)
    await first.consume(permit, order, now=NOW)
    second = DurablePermitLedger(engine, signer)
    with pytest.raises(PermitRejected, match="already_used"):
        await second.consume(permit, order, now=NOW)


async def test_second_permit_cannot_reauthorize_same_client_order(engine) -> None:
    signer = PermitSigner(KEY)
    ledger = DurablePermitLedger(engine, signer)
    order = trade()
    await ledger.consume(signer.issue(order, now=NOW), order, now=NOW)
    with pytest.raises(PermitRejected, match="order_already_authorized"):
        await ledger.consume(signer.issue(order, now=NOW), order, now=NOW)


async def test_only_paper_or_shadow_permits_are_accepted(engine) -> None:
    signer = PermitSigner(KEY)
    ledger = DurablePermitLedger(engine, signer)
    for stage in (ReleaseStage.CANARY, ReleaseStage.LIVE, ReleaseStage.REPLAY):
        order = trade(stage=stage)
        with pytest.raises(PermitRejected, match="stage_not_certified"):
            await ledger.consume(signer.issue(order, now=NOW), order, now=NOW)
    async with engine.connect() as conn:
        assert list((await conn.execute(select(consumed_permits))).all()) == []


async def test_tampering_fails_without_persisting_use(engine) -> None:
    signer = PermitSigner(KEY)
    ledger = DurablePermitLedger(engine, signer)
    order = trade()
    permit = signer.issue(order, now=NOW)
    altered = order.model_copy(update={"quantity": Decimal("0.002")})
    with pytest.raises(PermitRejected, match="intent_mismatch"):
        await ledger.consume(permit, altered, now=NOW)
    await ledger.consume(permit, order, now=NOW)


async def test_competing_consumers_only_one_wins(engine) -> None:
    signer = PermitSigner(KEY)
    order = trade()
    permit = signer.issue(order, now=NOW)
    first, second = (DurablePermitLedger(engine, signer) for _ in range(2))
    outcomes = await asyncio.gather(
        first.consume(permit, order, now=NOW),
        second.consume(permit, order, now=NOW),
        return_exceptions=True,
    )
    assert sum(outcome is None for outcome in outcomes) == 1
    assert sum(
        isinstance(outcome, PermitRejected) and outcome.code == "already_used"
        for outcome in outcomes
    ) == 1


async def test_signature_never_stored_in_durable_ledger(engine) -> None:
    signer = PermitSigner(KEY)
    order = trade()
    permit = signer.issue(order, now=NOW)
    await DurablePermitLedger(engine, signer).consume(permit, order, now=NOW)
    async with engine.connect() as conn:
        rows = (await conn.execute(select(consumed_permits))).mappings().all()
    assert len(rows) == 1
    assert "signature" not in rows[0]
    assert rows[0]["intent_fingerprint"] == order.fingerprint()
