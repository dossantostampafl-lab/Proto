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
