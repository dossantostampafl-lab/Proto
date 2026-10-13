"""End-to-end Phase 4 control-plane path: reviewed intent -> risk -> paper OMS."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.app import agent_surface, phase4_surface, risk_surface
from apps.api.app.event_state import JournalUnavailable
from apps.api.app.settings import Settings
from services.execution import Signal, SignalDirection, SignalSource
from services.execution.catalog import PAPER_INSTRUMENTS
from services.execution.oms import oms_metadata
from services.execution.paper_runtime import Phase4PaperRuntime
from services.execution.venue import Quote
from services.risk.durable_permits import permit_metadata
from services.risk.permits import PermitSigner

INSTRUMENT = PAPER_INSTRUMENTS["BINANCE_SPOT:BTCBRL"]
KEY = "s" * 32


@pytest.fixture
async def paper_account(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'control-plane.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(permit_metadata.create_all)
        await conn.run_sync(oms_metadata.create_all)

    def quote(_id: str) -> Quote:
        return Quote(
            bid=Decimal("349990"),
            ask=Decimal("350010"),
            observed_at=datetime.now(UTC) - timedelta(milliseconds=100),
        )

    worker = Phase4PaperRuntime(
        engine=engine,
        signer=PermitSigner(KEY.encode()),
        instruments=(INSTRUMENT,),
        quotes=quote,
        starting_balances={"BRL": Decimal("10000")},
        fee_bps=Decimal("2"),
        slippage_bps=Decimal("3"),
        reconciliation_interval_seconds=0.05,
    )
    await worker.start(now=datetime.now(UTC))
    yield worker, quote
    await worker.stop()
    await engine.dispose()


def configure(monkeypatch, worker, quote):
    settings = Settings(
        app_env="development",
        agent_pipeline_enabled=True,
        agent_reviewer="deterministic",
        canonical_paper_oms_enabled=True,
        persistence_enabled=True,
        risk_permit_signing_key=KEY,
    )
    monkeypatch.setattr(agent_surface, "settings", settings)
    monkeypatch.setattr(risk_surface, "settings", settings)
    monkeypatch.setattr(phase4_surface, "settings", settings)
    monkeypatch.setattr(phase4_surface, "_runtime", worker)
    monkeypatch.setattr(agent_surface, "live_quote", quote)
    monkeypatch.setattr(risk_surface, "live_quote", lambda _id, **_kw: quote(_id))
    agent_surface.reset_agent_state()
    risk_surface.reset_risk_state()
    return settings


def fresh_signal() -> Signal:
    now = datetime.now(UTC)
    return Signal(
        signal_id=uuid4(),
        source=SignalSource.TRADINGVIEW,
        strategy_id="tv-ema",
        instrument_id=INSTRUMENT.instrument_id,
        direction=SignalDirection.LONG,
        strength=0.9,
        timeframe="15",
        observed_at=now,
        received_at=now,
    )


async def test_agent_risk_permit_to_paper_oms_is_executed_and_reconciled(
    monkeypatch, paper_account
):
    worker, quote = paper_account
    configure(monkeypatch, worker, quote)
    agent_surface._signals.append(fresh_signal())
    decision, risk = await agent_surface._evaluate(INSTRUMENT.instrument_id)
    assert decision.proposal is not None
    assert risk is not None and risk.approved is True
    assert risk.permit is not None
    assert (await worker.adapter.balances())["BTC"] > 0
    assert (await worker.oms.reconcile())["ok"] is True
    assert (await worker.risk_account(now=datetime.now(UTC))).positions[
        INSTRUMENT.instrument_id
    ] > 0


async def test_audit_failure_blocks_execution_even_with_a_valid_risk_proposal(
    monkeypatch, paper_account
):
    worker, quote = paper_account
    configure(monkeypatch, worker, quote)

    async def audit_unavailable(**_kwargs):
        raise JournalUnavailable("audit storage unavailable")

    monkeypatch.setattr(phase4_surface, "record_operational_event", audit_unavailable)
    agent_surface._signals.append(fresh_signal())
    with pytest.raises(JournalUnavailable):
        await agent_surface._evaluate(INSTRUMENT.instrument_id)
    assert (await worker.adapter.balances()).get("BTC", Decimal("0")) == 0


def test_paper_execution_flag_requires_durable_storage_and_stable_permit_key():
    with pytest.raises(ValueError, match="CANONICAL_PAPER_OMS_ENABLED"):
        Settings(canonical_paper_oms_enabled=True)
    with pytest.raises(ValueError, match="RISK_PERMIT_SIGNING_KEY"):
        Settings(canonical_paper_oms_enabled=True, persistence_enabled=True)
    configured = Settings(
        canonical_paper_oms_enabled=True,
        persistence_enabled=True,
        risk_permit_signing_key=KEY,
    )
    assert configured.canonical_paper_oms_enabled is True


def test_paper_execution_audit_uses_durable_orchestration_engine_when_needed():
    from apps.api.app.event_state import choose_operational_journal_engine

    fallback_engine = object()
    assert choose_operational_journal_engine(
        persistence_engine=None,
        orchestration_engine=fallback_engine,
        canonical_paper_oms_enabled=True,
    ) is fallback_engine
    assert choose_operational_journal_engine(
        persistence_engine=None,
        orchestration_engine=fallback_engine,
        canonical_paper_oms_enabled=False,
    ) is None


async def test_canonical_oms_boot_refuses_memory_only_audit_journal(
    monkeypatch, paper_account
):
    worker, quote = paper_account
    configure(monkeypatch, worker, quote)
    monkeypatch.setattr(phase4_surface, "_runtime", None)
    monkeypatch.setattr(
        phase4_surface, "operational_journal", SimpleNamespace(store=None)
    )
    with pytest.raises(RuntimeError, match="durable audit"):
        await phase4_surface.start(worker.oms.engine, PermitSigner(KEY.encode()))
