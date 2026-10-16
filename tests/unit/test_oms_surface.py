from __future__ import annotations

import importlib.util
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from apps.api.app import agent_surface, main, oms_surface, risk_surface, signal_surface
from apps.api.app.event_state import operational_journal
from apps.api.app.settings import Settings
from services.oms.ledger import entry_id, ledger_table

SECRET = "w" * 32
TOKEN = "o" * 32
HEADERS = {"X-Proto-Operator-Token": TOKEN}


def configure(monkeypatch, **overrides) -> Settings:
    values = {
        "tradingview_webhook_secret": SECRET,
        "tradingview_ip_allowlist_enabled": False,
        "tradingview_instruments": "BINANCE_SPOT:BTCUSDT",
        "live_market_source": "BINANCE",
        "agent_pipeline_enabled": True,
        "oms_paper_execution_enabled": True,
        "oms_paper_slippage_bps": 0.0,
        "operator_api_token": TOKEN,
    }
    values.update(overrides)
    configured = Settings(**values)
    for module in (main, signal_surface, agent_surface, risk_surface, oms_surface):
        monkeypatch.setattr(module, "settings", configured)
    return configured


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(agent_surface.live_monitor, "snapshot", live_snapshot)
    for reset in (
        signal_surface.reset_signal_state,
        agent_surface.reset_agent_state,
        risk_surface.reset_risk_state,
        oms_surface.reset_oms_state,
    ):
        reset()
    yield
    for reset in (
        signal_surface.reset_signal_state,
        agent_surface.reset_agent_state,
        risk_surface.reset_risk_state,
        oms_surface.reset_oms_state,
    ):
        reset()


def live_snapshot(symbol: str):
    return {
        "symbol": symbol,
        "venue": "BINANCE",
        "bid": 62_000.00,
        "ask": 62_000.20,
        "timestamp": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }


def alert(direction: str = "LONG") -> str:
    return json.dumps(
        {
            "secret": SECRET,
            "alert_id": f"a-{time.time_ns()}",
            "strategy_id": "tv-ema",
            "instrument_id": "BINANCE_SPOT:BTCUSDT",
            "direction": direction,
            "strength": 0.9,
            "timeframe": "15",
            "fired_at": datetime.now(UTC).isoformat(),
        }
    )


def wait_for_orders(client: TestClient, count: int) -> list[dict]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        orders = client.get("/v1/oms/orders").json()["orders"]
        if len(orders) >= count:
            return orders
        time.sleep(0.01)
    raise AssertionError("orders did not arrive")


def test_signal_to_paper_fill_end_to_end(monkeypatch) -> None:
    configure(monkeypatch)
    with TestClient(main.app) as client:
        assert client.post("/v1/signals/tradingview", content=alert()).status_code == 200
        [order] = wait_for_orders(client, 1)
        ledger = client.get("/v1/oms/ledger").json()
        reconciliation = client.post("/v1/oms/reconcile", headers=HEADERS).json()

    assert order["status"] == "FILLED"
    assert order["side"] == "BUY"
    assert order["real_money_execution"] is False
    assert Decimal(ledger["balances"]["BTC"]["total"]) == Decimal(order["filled_quantity"])
    assert Decimal(ledger["balances"]["USDT"]["reserved"]) == 0
    assert ledger["mode"] == "PAPER"
    assert reconciliation["consistent"] is True
    events = [e.event_type for e in operational_journal.journal.recent(5)]
    assert events[-4:] == [
        "AGENT_DECISION",
        "RISK_DECISION",
        "ORDER_SUBMITTED",
        "ORDER_FILLED",
    ]


def test_sell_signal_exits_the_position_from_the_ledger(monkeypatch) -> None:
    configure(monkeypatch)
    with TestClient(main.app) as client:
        client.post("/v1/signals/tradingview", content=alert("LONG"))
        wait_for_orders(client, 1)
        client.post("/v1/signals/tradingview", content=alert("SHORT"))
        orders = wait_for_orders(client, 2)
        ledger = client.get("/v1/oms/ledger").json()

    assert orders[0]["side"] == "SELL"
    assert orders[0]["status"] == "FILLED"
    assert Decimal(ledger["balances"]["BTC"]["total"]) == 0


def test_execution_disabled_leaves_permits_unused(monkeypatch) -> None:
    configure(monkeypatch, oms_paper_execution_enabled=False)
    with TestClient(main.app) as client:
        client.post("/v1/signals/tradingview", content=alert())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if client.get("/v1/risk/decisions/recent").json()["decisions"]:
                break
            time.sleep(0.01)
        assert client.get("/v1/oms/orders").json()["orders"] == []


async def test_divergence_halts_the_venue(monkeypatch) -> None:
    configure(monkeypatch)
    _, _, adapter = await oms_surface.components()
    adapter._balances["USDT"] -= Decimal("1")  # venue drifts from the ledger
    result = await oms_surface.reconcile()
    assert result["consistent"] is False
    assert result["venue_halted"] is True
    assert "BINANCE_SPOT" in risk_surface.governor().snapshot()["killed_venues"]
    assert operational_journal.journal.recent(1)[0].event_type == "RECONCILIATION_DIVERGENCE"


async def test_account_state_marks_positions_and_blocks_without_marks(monkeypatch) -> None:
    configure(monkeypatch)
    _, ledger, _ = await oms_surface.components()
    state = await oms_surface.account_state("USDT")
    assert state.equity == Decimal("10000")
    assert state.gross_exposure == 0

    from services.execution import LedgerEntry, LedgerEntryKind, VenueId

    await ledger.post(
        [
            LedgerEntry(
                entry_id=entry_id("test", "btc"),
                account_id=oms_surface.ACCOUNT_ID,
                venue=VenueId.BINANCE_SPOT,
                asset="BTC",
                amount=Decimal("0.01"),
                kind=LedgerEntryKind.TRADE,
                reference_id="test",
                recorded_at=datetime.now(UTC),
            )
        ]
    )
    marked = await oms_surface.account_state("USDT")
    assert marked.gross_exposure == Decimal("620.0010")
    assert marked.positions["BINANCE_SPOT:BTCUSDT"] == Decimal("0.01")

    monkeypatch.setattr(agent_surface.live_monitor, "snapshot", lambda _s: None)
    unmarked = await oms_surface.account_state("USDT")
    assert unmarked.gross_exposure == Decimal("Infinity")
    assert unmarked.equity == unmarked.start_of_day_equity


async def test_opening_holdings_are_outside_the_proto_book(monkeypatch) -> None:
    """The testnet faucet's 1 BTC must not count as PROTO exposure against 10k of capital."""

    configure(monkeypatch)
    _, ledger, _ = await oms_surface.components()

    from services.execution import LedgerEntry, LedgerEntryKind, VenueId

    def entry(kind: LedgerEntryKind, amount: str) -> LedgerEntry:
        return LedgerEntry(
            entry_id=entry_id("test", kind.value, "btc"),
            account_id=oms_surface.ACCOUNT_ID,
            venue=VenueId.BINANCE_SPOT,
            asset="BTC",
            amount=Decimal(amount),
            kind=kind,
            reference_id="test",
            recorded_at=datetime.now(UTC),
        )

    await ledger.post([entry(LedgerEntryKind.OPENING_BALANCE, "1")])
    assert (await ledger.balances())["BTC"].opening == Decimal("1")
    opening_only = await oms_surface.account_state("USDT")
    assert opening_only.positions["BINANCE_SPOT:BTCUSDT"] == 0
    assert opening_only.gross_exposure == 0
    assert opening_only.equity == Decimal("10000")

    await ledger.post([entry(LedgerEntryKind.TRADE, "0.01")])
    traded = await oms_surface.account_state("USDT")
    assert traded.positions["BINANCE_SPOT:BTCUSDT"] == Decimal("0.01")
    assert traded.gross_exposure == Decimal("620.0010")


@pytest.mark.parametrize(
    ("btc_row", "expected_position"),
    [
        ({"total": "1.01", "opening": "1"}, Decimal("0.01")),
        # An executor that does not report the opening balance keeps the stricter reading.
        ({"total": "1.01"}, Decimal("1.01")),
    ],
)
async def test_executor_mode_reads_the_opening_balance_when_reported(
    monkeypatch, btc_row, expected_position
) -> None:
    configure(monkeypatch)

    class FakeExecutor:
        async def ledger(self) -> dict:
            return {"balances": {"USDT": {"total": "10000"}, "BTC": btc_row}}

    monkeypatch.setattr(oms_surface, "executor_mode", lambda: True)
    monkeypatch.setattr(oms_surface, "executor_client", lambda: FakeExecutor())
    state = await oms_surface.account_state("USDT")
    assert state.positions["BINANCE_SPOT:BTCUSDT"] == expected_position


async def test_checkpoints_saved_with_the_faucet_btc_do_not_read_as_a_loss(monkeypatch) -> None:
    """Start/peak saved with the faucet BTC in equity (~91k) must not trip the 1% / 5% locks."""

    configure(monkeypatch)
    _, ledger, _ = await oms_surface.components()
    await ledger.post([_opening_btc("1")])
    today = datetime.now(UTC).date()
    await oms_surface._checkpoint_store().put("USDT", (today, Decimal("91000"), Decimal("91000")))
    state = await oms_surface.account_state("USDT")
    assert state.equity == Decimal("10000")
    assert state.start_of_day_equity == Decimal("10000")
    assert state.peak_equity == Decimal("10000")
    # The rebase is a move to the own-book key: later reads keep that baseline, drawdown included.
    assert await oms_surface._checkpoint_store().get(oms_surface._book_key("USDT")) == (
        today,
        Decimal("10000"),
        Decimal("10000"),
    )


async def test_accounts_without_opening_holdings_keep_their_saved_checkpoint(monkeypatch) -> None:
    """The basis did not change: a loss already on the books must stay, with no re-baseline."""

    configure(monkeypatch)
    await oms_surface.components()
    today = datetime.now(UTC).date()
    store = oms_surface._checkpoint_store()
    await store.put("USDT", (today, Decimal("10500"), Decimal("10600")))
    state = await oms_surface.account_state("USDT")
    assert state.equity == Decimal("10000")
    assert state.start_of_day_equity == Decimal("10500")
    assert state.peak_equity == Decimal("10600")
    assert await store.get(oms_surface._book_key("USDT")) is None


async def test_unreachable_executor_falls_back_to_the_saved_baseline(monkeypatch) -> None:
    configure(monkeypatch)
    await oms_surface.components()
    today = datetime.now(UTC).date()
    await oms_surface._checkpoint_store().put("USDT", (today, Decimal("10500"), Decimal("10600")))

    async def unavailable(venue=None):
        raise oms_surface.ExecutorUnavailable("down")

    monkeypatch.setattr(oms_surface, "_totals", unavailable)
    state = await oms_surface.account_state("USDT")
    assert state.gross_exposure == Decimal("Infinity")
    assert state.equity == Decimal("10500")


async def _outage(monkeypatch) -> None:
    async def unavailable(venue=None):
        raise oms_surface.ExecutorUnavailable("down")

    monkeypatch.setattr(oms_surface, "_totals", unavailable)


async def test_outage_uses_the_same_basis_rule_for_an_account_without_opening_holdings(
    monkeypatch,
) -> None:
    """A stale <quote>:own row must not hide a loss kept on the plain checkpoint."""

    configure(monkeypatch)
    await oms_surface.components()
    today = datetime.now(UTC).date()
    store = oms_surface._checkpoint_store()
    await store.put("USDT", (today, Decimal("10500"), Decimal("10600")))
    await store.put(oms_surface._book_key("USDT"), (today, Decimal("9000"), Decimal("9500")))
    await oms_surface.account_state("USDT")  # a good read records that nothing was held at open
    await _outage(monkeypatch)
    state = await oms_surface.account_state("USDT")
    assert state.gross_exposure == Decimal("Infinity")
    assert state.equity == Decimal("10500")
    assert state.peak_equity == Decimal("10600")


async def test_outage_uses_the_own_book_for_an_account_with_opening_holdings(monkeypatch) -> None:
    configure(monkeypatch)
    _, ledger, _ = await oms_surface.components()
    await ledger.post([_opening_btc("1")])
    today = datetime.now(UTC).date()
    store = oms_surface._checkpoint_store()
    await store.put("USDT", (today, Decimal("91000"), Decimal("91000")))
    await oms_surface.account_state("USDT")  # rebases onto <quote>:own and records the opening
    await _outage(monkeypatch)
    state = await oms_surface.account_state("USDT")
    assert state.equity == Decimal("10000")
    assert state.peak_equity == Decimal("10000")


async def test_cold_start_outage_takes_the_more_conservative_checkpoint(monkeypatch) -> None:
    """With the basis unknown: lower start of day, higher peak, never the flattering pair."""

    configure(monkeypatch)
    await oms_surface.components()
    today = datetime.now(UTC).date()
    store = oms_surface._checkpoint_store()
    await store.put("USDT", (today, Decimal("10500"), Decimal("10600")))
    await store.put(oms_surface._book_key("USDT"), (today, Decimal("10000"), Decimal("10100")))
    await _outage(monkeypatch)
    state = await oms_surface.account_state("USDT")
    assert state.equity == Decimal("10000")
    assert state.peak_equity == Decimal("10600")


def _opening_btc(amount: str):
    from services.execution import LedgerEntry, LedgerEntryKind, VenueId

    return LedgerEntry(
        entry_id=entry_id("test", "opening", "btc"),
        account_id=oms_surface.ACCOUNT_ID,
        venue=VenueId.BINANCE_SPOT,
        asset="BTC",
        amount=Decimal(amount),
        kind=LedgerEntryKind.OPENING_BALANCE,
        reference_id="test",
        recorded_at=datetime.now(UTC),
    )


def test_operator_routes_require_the_token(monkeypatch) -> None:
    configure(monkeypatch)
    client = TestClient(main.app)
    assert client.post("/v1/oms/reconcile").status_code == 401
    assert client.post("/v1/oms/orders/PXnope/cancel").status_code == 401
    assert client.post("/v1/oms/orders/PXnope/cancel", headers=HEADERS).status_code == 404
    assert client.post("/v1/oms/orders/PXnope/resolve", headers=HEADERS).status_code == 404


def test_ledger_migration_matches_the_runtime_table(monkeypatch) -> None:
    path = Path("migrations/versions/0008_ledger_entries.py")
    spec = importlib.util.spec_from_file_location("migration_0008", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0007_operational_journal"

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        columns = {c["name"] for c in inspect(connection).get_columns(migration.TABLE_NAME)}
        assert columns == set(ledger_table.columns.keys())
        migration.downgrade()
        assert migration.TABLE_NAME not in inspect(connection).get_table_names()


async def test_equity_checkpoints_survive_a_restart(monkeypatch, tmp_path) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from services.oms import SqlCheckpoints, ledger_metadata

    configure(monkeypatch)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'oms.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(ledger_metadata.create_all)
    monkeypatch.setattr(oms_surface, "persistence_engine", engine)
    try:
        first = await oms_surface.account_state("USDT")
        assert first.peak_equity == Decimal("10000")

        # An earlier high and an earlier start of day were recorded before the restart.
        checkpoints = SqlCheckpoints(engine, account_id=oms_surface.ACCOUNT_ID)
        today = datetime.now(UTC).date()
        key = "USDT"
        await checkpoints.put(key, (today, Decimal("10080"), Decimal("10600")))
        oms_surface.reset_oms_state()
        restarted = await oms_surface.account_state("USDT")
        assert restarted.start_of_day_equity == Decimal("10080")
        assert restarted.peak_equity == Decimal("10600")

        # A new UTC day resets the start of day but never the peak.
        await checkpoints.put(key, (today - timedelta(days=1), Decimal("9000"), Decimal("10600")))
        oms_surface.reset_oms_state()
        next_day = await oms_surface.account_state("USDT")
        assert next_day.start_of_day_equity == Decimal("10000")
        assert next_day.peak_equity == Decimal("10600")
        assert await checkpoints.get(key) == (today, Decimal("10000"), Decimal("10600"))
    finally:
        await engine.dispose()


def test_oms_state_migration_matches_the_runtime_tables(monkeypatch) -> None:
    from services.oms.store import consumed_permits_table, equity_checkpoints_table, orders_table

    path = Path("migrations/versions/0009_oms_state.py")
    spec = importlib.util.spec_from_file_location("migration_0009", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0008_ledger_entries"

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    runtime = {t.name: t for t in (consumed_permits_table, orders_table, equity_checkpoints_table)}
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        for name, table in runtime.items():
            columns = {c["name"] for c in inspect(connection).get_columns(name)}
            assert columns == set(table.columns.keys()), name
        migration.downgrade()
        assert not set(runtime) & set(inspect(connection).get_table_names())
