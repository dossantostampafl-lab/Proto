"""XP/B3 through the MT5 bridge: demo-only orders, idempotent ids, fills from deals, grids."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
import pytest

from apps.mt5_bridge import gateway as g
from apps.mt5_bridge.service import create_app
from services.execution import OrderStatus, OrderType, TimeInForce, VenueId
from services.execution.venue import VenueOrder
from services.venues.binance import VenueOutcomeUnknown
from services.venues.credentials import VenueEnvironment
from services.venues.xp_mt5 import XpMt5Adapter, bridge_client, load_instruments

TOKEN = "b" * 40


class FakeTerminal:
    def __init__(self, *, mode: int = g.ACCOUNT_TRADE_MODE_DEMO) -> None:
        self.mode = mode
        self.sent: list[dict] = []
        self.active: dict[int, dict] = {}
        self.history: dict[int, dict] = {}
        self.fills: dict[int, list[dict]] = {}
        self.next_ticket = 1000

    def account(self):
        return {
            "trade_mode": self.mode,
            "server": "XPMT5-DEMO",
            "currency": "BRL",
            "balance": 10000.0,
        }

    def symbol(self, name):
        if name not in ("PETR4F", "BOVA11F"):
            return None
        return {
            "trade_tick_size": 0.01,
            "volume_min": 1.0,
            "volume_step": 1.0,
            "currency_profit": "BRL",
            "visible": True,
        }

    def tick(self, name):
        return (
            {"bid": 38.10, "ask": 38.12, "time_msc": 1_760_000_000_000}
            if name == "PETR4F"
            else None
        )

    def positions(self):
        return [{"symbol": "PETR4F", "type": g.ORDER_TYPE_BUY, "volume": 30.0}]

    def send(self, request):
        self.sent.append(request)
        if request["action"] == g.TRADE_ACTION_REMOVE:
            order = self.active.pop(request["order"])
            self.history[request["order"]] = {**order, "state": g.ORDER_STATE_CANCELED}
            return {"retcode": g.TRADE_RETCODE_DONE, "order": request["order"]}
        ticket, self.next_ticket = self.next_ticket, self.next_ticket + 1
        self.active[ticket] = {
            "ticket": ticket,
            "state": g.ORDER_STATE_PLACED,
            "time_setup": 1_760_000_000,
        }
        return {"retcode": g.TRADE_RETCODE_PLACED, "order": ticket}

    def active_order(self, ticket):
        return self.active.get(ticket)

    def history_order(self, ticket):
        return self.history.get(ticket)

    def deals(self, order_ticket):
        return self.fills.get(order_ticket, [])

    def fill(self, ticket, volume, price, commission=0.0):
        order = self.active.pop(ticket)
        self.history[ticket] = {**order, "state": g.ORDER_STATE_FILLED, "time_done": 1_760_000_100}
        self.fills[ticket] = [
            {"volume": volume, "price": price, "commission": -commission, "order": ticket}
        ]


def wire(terminal: FakeTerminal, tmp_path, *, token: str = TOKEN) -> httpx.AsyncClient:
    app = create_app(terminal, token=TOKEN, store_path=tmp_path / "bridge.sqlite3")
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://bridge",
        headers={"Authorization": f"Bearer {token}"},
    )


def order(client_order_id: str = "PXabc", *, quantity: str = "30") -> VenueOrder:
    return VenueOrder(
        client_order_id=client_order_id,
        instrument_id="XP_B3:PETR4F",
        side="BUY",
        quantity=Decimal(quantity),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("38.10"),
        time_in_force=TimeInForce.DAY,
    )


def connect(client):
    return asyncio.run(XpMt5Adapter.connect(client, symbols=["PETR4F", "BOVA11F", "NOPE3"]))


def test_a_demo_login_is_the_testnet_and_grids_come_from_the_terminal(tmp_path) -> None:
    adapter = connect(wire(FakeTerminal(), tmp_path))
    assert adapter.venue is VenueId.XP_B3 and adapter.environment is VenueEnvironment.TESTNET
    assert adapter.real_money_execution is False
    petr = adapter.instruments["XP_B3:PETR4F"]
    assert (petr.tick_size, petr.lot_size, petr.quote_asset, petr.timezone) == (
        Decimal("0.01"),
        Decimal("1"),
        "BRL",
        "America/Sao_Paulo",
    )
    assert "XP_B3:NOPE3" not in adapter.instruments  # unknown symbols are left out
    assert adapter.instruments["XP_B3:BOVA11F"].asset_class.value == "ETF"


def test_a_real_login_is_live_for_the_oms_and_the_bridge_refuses_its_orders(tmp_path) -> None:
    terminal = FakeTerminal(mode=g.ACCOUNT_TRADE_MODE_REAL)
    adapter = connect(wire(terminal, tmp_path))
    assert adapter.environment is VenueEnvironment.LIVE and adapter.real_money_execution is True
    report = asyncio.run(adapter.submit(order()))
    assert report.status is OrderStatus.REJECTED and "REAL account" in (report.reason or "")
    assert terminal.sent == []  # nothing reached the terminal


def test_the_bridge_needs_its_token(tmp_path) -> None:
    client = wire(FakeTerminal(), tmp_path, token="x" * 40)
    response = asyncio.run(client.get("/v1/balances"))
    assert response.status_code == 401
    with pytest.raises(ValueError):
        create_app(FakeTerminal(), token="short", store_path=tmp_path / "x.sqlite3")


def test_a_limit_order_rests_then_fills_with_fees_and_a_resubmit_places_nothing(tmp_path) -> None:
    terminal = FakeTerminal()
    adapter = connect(wire(terminal, tmp_path))
    placed = asyncio.run(adapter.submit(order()))
    assert placed.status is OrderStatus.ACKNOWLEDGED and placed.venue_order_id == "1000"
    request = terminal.sent[0]
    assert request["action"] == g.TRADE_ACTION_PENDING and request["type"] == g.ORDER_TYPE_BUY_LIMIT
    assert request["type_time"] == g.ORDER_TIME_DAY and request["volume"] == 30.0
    again = asyncio.run(adapter.submit(order()))
    assert again.venue_order_id == "1000" and len(terminal.sent) == 1
    terminal.fill(1000, 30.0, 38.10, commission=1.5)
    filled = asyncio.run(adapter.query("PXabc"))
    assert filled.status is OrderStatus.FILLED and filled.filled_quantity == Decimal("30")
    assert (
        filled.average_price == Decimal("38.1")
        and filled.fee == Decimal("1.5")
        and filled.fee_asset == "BRL"
    )


def test_cancel_removes_the_pending_order_and_unknown_ids_are_not_found(tmp_path) -> None:
    terminal = FakeTerminal()
    adapter = connect(wire(terminal, tmp_path))
    asyncio.run(adapter.submit(order("PXcancel")))
    cancelled = asyncio.run(adapter.cancel("PXcancel"))
    assert cancelled.status is OrderStatus.CANCELED
    assert terminal.sent[-1] == {"action": g.TRADE_ACTION_REMOVE, "order": 1000}
    assert asyncio.run(adapter.query("PXnever")) is None
    with pytest.raises(LookupError):
        asyncio.run(adapter.cancel("PXnever"))


def test_balances_and_quotes_read_through_the_bridge(tmp_path) -> None:
    adapter = connect(wire(FakeTerminal(), tmp_path))
    assert asyncio.run(adapter.balances()) == {"BRL": Decimal("10000.0"), "PETR4F": Decimal("30.0")}
    quotes = asyncio.run(adapter.quotes(["PETR4F", "VALE3"]))
    assert set(quotes) == {"PETR4F"} and quotes["PETR4F"]["bid"] == "38.1"


def test_a_bridge_outage_is_an_unknown_outcome_never_a_rejection() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    client = httpx.AsyncClient(transport=httpx.MockTransport(broken), base_url="http://bridge")
    adapter = XpMt5Adapter(client, environment=VenueEnvironment.TESTNET, instruments={})
    with pytest.raises(VenueOutcomeUnknown):
        asyncio.run(adapter.query("PXabc"))
    with pytest.raises(VenueOutcomeUnknown):
        asyncio.run(load_instruments(client, ["PETR4F"]))
    assert bridge_client("http://10.20.2.12:8200/", "t").base_url == httpx.URL(
        "http://10.20.2.12:8200"
    )
