"""Durable control-plane state survives a restart; an unreachable store fails closed.

Each "restart" disposes the engine, drops every in-process singleton and reconnects a new
engine to the same SQLite file, which is what a deploy does to the Postgres-backed API.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import Response
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.app import (
    control_state,
    core_routes,
    event_surface,
    oms_surface,
    promotion_surface,
    risk_surface,
    roster_surface,
    watchdog_surface,
)
from apps.api.app.event_state import operational_journal, record_operational_event
from apps.api.app.persistence import init_control_database
from apps.api.app.settings import Settings
from services.events import EventJournal
from services.execution import OrderType, TradeIntent, VenueId
from services.execution.venue import Quote
from services.oms import SqlCheckpoints, SqlPermitLedger
from services.promotion import EvidenceKind, Scope
from services.risk import KillScope, PermitLedger, PermitRejected, PermitSigner

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "o" * 32
SIGNING_KEY = "s" * 32


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    settings = Settings(operator_api_token=TOKEN, risk_permit_signing_key=SIGNING_KEY)
    for module in (risk_surface, oms_surface, roster_surface, promotion_surface):
        monkeypatch.setattr(module, "settings", settings)
    original_store = control_state.control_store
    original_journal = operational_journal.journal
    original_journal_store = operational_journal.store
    original_integrity = operational_journal.integrity
    operational_journal.journal = EventJournal()
    _reset()
    yield settings
    _reset()
    control_state.control_store = original_store
    operational_journal.journal = original_journal
    operational_journal.store = original_journal_store
    operational_journal.integrity = original_integrity


def _reset() -> None:
    risk_surface.reset_risk_state()
    oms_surface.reset_oms_state()
    promotion_surface.reset_promotion_state()
    roster_surface.reset_roster_state()
    watchdog_surface.reset_watchdog_state()


async def _boot(path: Path):
    """One process start against the database file: what main.lifespan does."""

    _reset()
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    store = control_state.configure_control_store(engine)
    assert await store.start() is True
    await risk_surface.restore_controls()
    await watchdog_surface.restore_watchdog()
    return engine


def _intent() -> TradeIntent:
    return TradeIntent(
        intent_id=uuid4(),
        instrument_id="BINANCE_SPOT:BTCUSDT",
        venue=VenueId.BINANCE_SPOT,
        side="BUY",
        quantity=Decimal("0.002"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("62000.20"),
        strategy_id="hybrid-fusion-v1",
        model_version="fusion-v1",
        signal_ids=(uuid4(),),
        created_at=datetime.now(UTC),
    )


def _fresh_quote(*_args, **_kwargs) -> Quote:
    return Quote(bid=Decimal("62000.00"), ask=Decimal("62000.20"), observed_at=datetime.now(UTC))


# --- settings boundary ---------------------------------------------------------------------


def test_simulation_persistence_is_still_refused_in_production_live_monitoring() -> None:
    with pytest.raises(ValidationError, match="PERSISTENCE_ENABLED must be false"):
        Settings(
            app_env="production",
            operator_api_token=TOKEN,
            system_mode="LIVE_MONITORING",
            persistence_enabled=True,
            database_url="postgresql+asyncpg://proto:proto@db/proto",
        )
    allowed = Settings(
        app_env="production",
        operator_api_token=TOKEN,
        system_mode="LIVE_MONITORING",
        control_persistence_enabled=True,
        database_url="postgresql+asyncpg://proto:proto@db/proto",
    )
    assert allowed.control_persistence_active is True
    assert allowed.persistence_enabled is False


def test_control_persistence_derivation() -> None:
    assert Settings().control_persistence_active is False
    assert Settings(control_persistence_enabled=True).control_persistence_active is True
    production = Settings(
        app_env="production",
        operator_api_token=TOKEN,
        database_url="postgresql+asyncpg://proto:proto@db/proto",
    )
    assert production.control_persistence_active is True
    ephemeral = Settings(app_env="production", operator_api_token=TOKEN)
    assert ephemeral.control_persistence_active is False
    assert ephemeral.database_process_local is True


def test_image_turns_control_persistence_on_without_simulation_persistence() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "CONTROL_PERSISTENCE_ENABLED=true" in dockerfile
    assert "\n    PERSISTENCE_ENABLED=true" not in dockerfile


async def test_control_schema_never_creates_simulation_tables(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'control.db'}")
    try:
        await init_control_database(engine)
        async with engine.connect() as connection:
            tables = set(await connection.run_sync(lambda c: inspect(c).get_table_names()))
    finally:
        await engine.dispose()
    assert {"control_state", "operational_journal", "promotion_scopes"} <= tables
    assert {"oms_equity_checkpoints", "oms_consumed_permits", "agent_roster"} <= tables
    assert not {"simulation_sessions", "simulation_fills"} & tables


def test_control_state_migration_matches_the_runtime_table(monkeypatch) -> None:
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    from services.risk import control_state_table

    path = ROOT / "migrations/versions/0013_control_state.py"
    spec = importlib.util.spec_from_file_location("migration_0013", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0012_agent_roster"
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        columns = {c["name"] for c in inspect(connection).get_columns("control_state")}
        assert columns == set(control_state_table.columns.keys())
        migration.downgrade()
        assert "control_state" not in inspect(connection).get_table_names()


# --- survive a restart ------------------------------------------------------------------------


async def test_journal_head_survives_a_restart_and_is_labelled_durable(tmp_path) -> None:
    db = tmp_path / "control.db"
    engine = await _boot(db)
    for n in range(3):
        await record_operational_event(event_type="TEST_EVENT", source="test", payload={"n": n})
    head = (operational_journal.journal.head_sequence, operational_journal.journal.head_hash)
    await engine.dispose()

    engine = await _boot(db)
    try:
        assert (
            operational_journal.journal.head_sequence,
            operational_journal.journal.head_hash,
        ) == head
        body = await event_surface.event_journal_verify(Response())
        assert body["chain_valid"] is True
        assert body["scope"] == "durable_history"
        assert body["persistence_enabled"] is True
        assert body["durable"] is True
        assert body["control_plane"]["status"] == "DURABLE"
        await record_operational_event(event_type="TEST_EVENT", source="test", payload={"n": 3})
        assert operational_journal.journal.head_sequence == head[0] + 1
    finally:
        await engine.dispose()


async def test_kill_switches_and_lockdown_survive_a_restart(tmp_path) -> None:
    db = tmp_path / "control.db"
    engine = await _boot(db)
    gov = await risk_surface.governor_ready()
    gov.trigger_kill(KillScope.GLOBAL)
    gov.trigger_kill(KillScope.VENUE, "BINANCE_SPOT")
    gov.restore({"lockdown": "DRAWDOWN"})  # what a latched breach leaves behind
    assert await risk_surface.persist_controls() is True
    await engine.dispose()

    engine = await _boot(db)
    try:
        controls = risk_surface.governor().snapshot()
        assert controls == {
            "global_kill": True,
            "killed_venues": ["BINANCE_SPOT"],
            "killed_strategies": [],
            "lockdown": "DRAWDOWN",
        }
        assert risk_surface.risk_status()["controls_persistence"]["durable"] is True
        # An operator release is saved too: the next start does not bring it back.
        await risk_surface.change_kill_switch(
            risk_surface.KillSwitchRequest(scope=KillScope.GLOBAL, action="RELEASE")
        )
    finally:
        await engine.dispose()

    engine = await _boot(db)
    try:
        controls = risk_surface.governor().snapshot()
        assert controls["global_kill"] is False
        assert controls["lockdown"] == "DRAWDOWN"
        assert controls["killed_venues"] == ["BINANCE_SPOT"]
    finally:
        await engine.dispose()


async def test_equity_checkpoints_use_the_control_store_and_survive(tmp_path) -> None:
    db = tmp_path / "control.db"
    engine = await _boot(db)
    store = oms_surface._checkpoint_store()
    assert isinstance(store, SqlCheckpoints)
    today = datetime.now(UTC).date()
    await store.put("USDT", (today, Decimal("10080.123456789"), Decimal("10600.5")))
    await engine.dispose()

    engine = await _boot(db)
    try:
        restored = oms_surface._checkpoint_store()
        assert await restored.get("USDT") == (
            today,
            Decimal("10080.123456789"),
            Decimal("10600.5"),
        )
        account = await oms_surface.account_state("USDT")
        assert account.start_of_day_equity == Decimal("10080.123456789")
        assert account.peak_equity == Decimal("10600.5")
    finally:
        await engine.dispose()


async def test_permits_stay_single_use_across_a_restart(tmp_path) -> None:
    db = tmp_path / "control.db"
    signer = PermitSigner(SIGNING_KEY.encode(), ttl=timedelta(seconds=60))
    trade = _intent()
    now = datetime.now(UTC)
    permit = signer.issue(trade, now=now)

    engine = await _boot(db)
    oms, _, _ = await oms_surface.components()
    assert isinstance(oms._permits, SqlPermitLedger)
    await oms._permits.consume(permit, trade, now=now)
    await engine.dispose()

    engine = await _boot(db)
    try:
        oms, _, _ = await oms_surface.components()
        with pytest.raises(PermitRejected, match="already_used"):
            await oms._permits.consume(permit, trade, now=now)
        # A fresh verifier (new process) refuses it as well.
        with pytest.raises(PermitRejected, match="already_used"):
            await SqlPermitLedger(PermitLedger(signer), engine).consume(permit, trade, now=now)
    finally:
        await engine.dispose()


async def test_promotion_coverage_survives_a_restart(tmp_path) -> None:
    db = tmp_path / "control.db"
    scope = Scope(
        account="paper-binance-spot",
        venue="BINANCE_SPOT",
        strategy_id="hybrid-fusion-v1",
        model_version="fusion-v1",
        credential_id="paper",
    )
    engine = await _boot(db)
    record = await promotion_surface.registry().add_evidence(
        scope, EvidenceKind.SHADOW_COMPARISON, reference="shadow:run-1", passed=True, by="test"
    )
    clean_since = record.clean_since
    await engine.dispose()

    engine = await _boot(db)
    try:
        restored = await promotion_surface.registry().get(scope)
        assert restored.clean_since == clean_since
        assert [e.reference for e in restored.evidence] == ["shadow:run-1"]
    finally:
        await engine.dispose()


async def test_watchdog_coverage_survives_a_quick_restart(tmp_path) -> None:
    db = tmp_path / "control.db"
    engine = await _boot(db)
    now = datetime.now(UTC)
    watchdog_surface.state().beat("wd-1", {}, now - timedelta(hours=5))
    for seconds in range(5 * 3600 - 20, 0, -20):
        watchdog_surface.state().beat("wd-1", {}, now - timedelta(seconds=seconds))
    await watchdog_surface._persist()
    covered_since = watchdog_surface.state().covered_since
    await engine.dispose()

    engine = await _boot(db)
    try:
        state = watchdog_surface.state()
        assert state.covered_since == covered_since
        assert state.coverage(now) >= timedelta(hours=4, minutes=59)
        assert watchdog_surface.watchdog_status()["coverage_durable"] is True
    finally:
        await engine.dispose()


async def test_agent_roster_uses_the_control_store_and_survives(tmp_path) -> None:
    db = tmp_path / "control.db"
    engine = await _boot(db)
    seeded = await roster_surface.ready()
    ids = sorted(agent.agent_id for agent in seeded.agents())
    assert ids
    await engine.dispose()

    engine = await _boot(db)
    try:
        book = roster_surface.roster()
        await book.load()
        assert sorted(agent.agent_id for agent in book.agents()) == ids
    finally:
        await engine.dispose()


# --- fail closed --------------------------------------------------------------------------------


async def test_unreachable_store_fails_closed_and_says_so(tmp_path, monkeypatch) -> None:
    # A database file inside a directory that does not exist cannot be opened.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'missing' / 'control.db'}")
    try:
        store = control_state.configure_control_store(engine)
        assert await store.start() is False
        assert store.status is control_state.ControlStatus.UNAVAILABLE
        assert store.accepts_new_risk() is False

        # No silent fallback to memory: no permit, even with a fresh quote and clean controls.
        monkeypatch.setattr(risk_surface, "live_quote", _fresh_quote)
        decision = await risk_surface.assess(_intent())
        assert decision.approved is False
        assert decision.permit is None
        assert decision.rejections == ("control_state_available",)

        # Releases cannot be applied while the state cannot be saved.
        gov = risk_surface.governor()
        gov.trigger_kill(KillScope.GLOBAL)
        with pytest.raises(risk_surface.HTTPException) as refused:
            await risk_surface.change_kill_switch(
                risk_surface.KillSwitchRequest(scope=KillScope.GLOBAL, action="RELEASE")
            )
        assert refused.value.status_code == 503
        assert gov.snapshot()["global_kill"] is True

        # Truthful labels on /ready and /events/journal/verify.
        response = Response()
        ready = await core_routes.ready(response)
        assert response.status_code == 503
        assert ready["status"] == "not_ready"
        assert ready["control_plane"]["durable"] is False
        assert ready["control_plane"]["status"] == "UNAVAILABLE"
        verify_response = Response()
        verify = await event_surface.event_journal_verify(verify_response)
        assert verify["durable"] is False
        assert verify["chain_valid"] is False
        assert verify_response.status_code == 409
        # The journal refuses writes rather than forking the chain from an empty head.
        with pytest.raises(Exception, match="UNVERIFIED"):
            await record_operational_event(event_type="X", source="test", payload={})
    finally:
        await engine.dispose()


async def test_store_recovers_once_the_database_answers(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "later"
    engine = create_async_engine(f"sqlite+aiosqlite:///{folder / 'control.db'}")
    try:
        store = control_state.configure_control_store(engine)
        assert await store.start() is False
        folder.mkdir()
        monkeypatch.setattr(control_state, "RETRY_INTERVAL", 0.0)
        assert await store.ensure() is True
        assert store.status is control_state.ControlStatus.DURABLE
        assert store.accepts_new_risk() is True
    finally:
        await engine.dispose()


def test_memory_mode_is_labelled_as_not_durable() -> None:
    store = control_state.configure_control_store(None)
    snapshot = store.snapshot()
    assert snapshot["status"] == "MEMORY"
    assert snapshot["durable"] is False
    assert snapshot["scope"] == "process_memory"
    assert store.accepts_new_risk() is True
