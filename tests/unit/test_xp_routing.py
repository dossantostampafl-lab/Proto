"""API routing to the XP executor: off unless configured, one venue never touches the other."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from apps.api.app import (
    agent_surface,
    b3_quotes,
    instrument_registry,
    main,
    market_quotes,
    oms_surface,
    risk_surface,
    signal_surface,
)
from apps.api.app import settings as settings_module
from apps.api.app.settings import Settings
from services.execution import OrderType, TimeInForce, TradeIntent, VenueId
from services.execution.catalog import B3_INSTRUMENTS, PAPER_INSTRUMENTS
from services.execution.venue import Quote

OPERATOR = "o" * 32
BINANCE_TOKEN = "e" * 32
XP_TOKEN = "x" * 32
HEADERS = {"X-Proto-Operator-Token": OPERATOR}
PETR = "XP_B3:PETR4F"
BTC = "BINANCE_SPOT:BTCUSDT"

XP_ON = {
    "oms_execution_backend": "executor",
    "executor_url": "http://binance-executor:8100",
    "executor_api_token": BINANCE_TOKEN,
    "xp_executor_url": "http://xp-executor:8100",
    "xp_executor_api_token": XP_TOKEN,
    "risk_permit_signing_key": "r" * 32,
}
XP_OFF = {k: v for k, v in XP_ON.items() if not k.startswith("xp_")}


def configure(monkeypatch, **overrides) -> Settings:
    values = {"operator_api_token": OPERATOR, "live_market_source": "BINANCE", **overrides}
    configured = Settings(**values)
    modules = (
        main,
        signal_surface,
        agent_surface,
        risk_surface,
        oms_surface,
        instrument_registry,
        settings_module,
    )
    for module in modules:
        monkeypatch.setattr(module, "settings", configured)
    return configured


@pytest.fixture(autouse=True)
def _reset():
    def reset() -> None:
        signal_surface.reset_signal_state()
        agent_surface.reset_agent_state()
        risk_surface.reset_risk_state()
        oms_surface.reset_oms_state()
        b3_quotes.install(None)

    reset()
    yield
    reset()


class FakeClient:
    """Stands in for one venue's ExecutorClient and records what reached it."""

    def __init__(self, name: str, *, balances=None, consistent=True, halted=False) -> None:
        self.name, self.consistent, self.halted = name, consistent, halted
        self.balances = balances or {}
        self.calls: list[tuple] = []
        self.quote_body: dict = {}

    async def submit(self, intent, permit):
        self.calls.append(("submit", intent.instrument_id))
        order = {"status": "FILLED", "client_order_id": f"c-{self.name}"}
        return {"order": order, "venue_halted": self.halted}

    async def ledger(self):
        self.calls.append(("ledger",))
        return {"account_id": f"acct-{self.name}", "balances": self.balances}

    async def reconcile(self):
        self.calls.append(("reconcile",))
        divergences = [] if self.consistent else [{"asset": "BRL"}]
        return {"consistent": self.consistent, "divergences": divergences}

    async def orders(self, limit: int = 50):
        self.calls.append(("orders",))
        return {"orders": [{"client_order_id": f"o-{self.name}", "status": "FILLED"}]}

    async def order_action(self, client_order_id: str, action: str):
        self.calls.append((action, client_order_id))
        return {"order": {"client_order_id": client_order_id, "status": "CANCELED"}}

    async def release_halt(self):
        self.calls.append(("release",))
        return {"consistent": True}

    async def quotes(self, names):
        self.calls.append(("quotes", tuple(names)))
        return {"quotes": self.quote_body}


def wire(monkeypatch, binance: FakeClient, xp: FakeClient) -> None:
    clients = {VenueId.BINANCE_SPOT: binance, VenueId.XP_B3: xp}

    def pick(venue: VenueId = VenueId.BINANCE_SPOT):
        return clients[venue]

    monkeypatch.setattr(oms_surface, "executor_client", pick)


def intent(instrument_id: str, venue: VenueId) -> TradeIntent:
    xp = venue is VenueId.XP_B3
    return TradeIntent.model_validate(
        {
            "intent_id": uuid4(),
            "instrument_id": instrument_id,
            "venue": venue,
            "side": "BUY",
            "quantity": Decimal("10") if xp else Decimal("0.01"),
            "order_type": OrderType.LIMIT,
            "limit_price": Decimal("38.12") if xp else Decimal("62100.00"),
            "time_in_force": TimeInForce.DAY if xp else TimeInForce.IOC,
            "strategy_id": "hybrid-fusion-v1",
            "model_version": "fusion-v1",
            "signal_ids": (uuid4(),),
            "created_at": datetime.now(UTC),
        }
    )


def install_b3_quote(bid: str = "38.10", ask: str = "38.12", *, age: float = 1.0) -> None:
    feed = b3_quotes.B3QuoteFeed(fetch=None, symbols=["PETR4F"])
    at = datetime.now(UTC) - timedelta(seconds=age)
    feed._quotes["PETR4F"] = Quote(bid=Decimal(bid), ask=Decimal(ask), observed_at=at)
    b3_quotes.install(feed)


# ---------- configuration ----------


def test_without_xp_settings_the_api_knows_nothing_about_b3(monkeypatch) -> None:
    configured = configure(monkeypatch, **XP_OFF)
    assert configured.xp_routing_enabled is False
    assert instrument_registry.active_instruments() is PAPER_INSTRUMENTS
    assert not any(key.startswith("XP_B3:") for key in PAPER_INSTRUMENTS)
    assert oms_surface.venue_for_quote("BRL") is VenueId.BINANCE_SPOT


def test_with_xp_settings_b3_joins_the_catalog_and_brl_belongs_to_xp(monkeypatch) -> None:
    configured = configure(monkeypatch, **XP_ON)
    assert configured.xp_routing_enabled is True
    active = instrument_registry.active_instruments()
    assert set(B3_INSTRUMENTS) <= set(active) and set(PAPER_INSTRUMENTS) <= set(active)
    assert oms_surface.venue_for_quote("BRL") is VenueId.XP_B3
    assert oms_surface.venue_for_quote("USDT") is VenueId.BINANCE_SPOT
    assert B3_INSTRUMENTS[PETR].lot_size == Decimal("1")


@pytest.mark.parametrize(
    "overrides",
    [
        {"oms_execution_backend": "paper"},  # XP routing needs the executor backend
        {"xp_executor_api_token": None},
        {"xp_executor_url": None},
        {"xp_executor_api_token": "short"},
        {"xp_executor_api_token": BINANCE_TOKEN},  # must differ from the Binance executor token
        {"xp_executor_api_token": OPERATOR},  # and from the operator token
    ],
)
def test_xp_settings_fail_closed_when_incomplete_or_reused(overrides) -> None:
    with pytest.raises(ValidationError):
        Settings(operator_api_token=OPERATOR, **{**XP_ON, **overrides})


# ---------- the B3 quote feed ----------


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"bid": "38.12", "ask": "38.10", "observed_at": "2026-10-16T18:39:25+00:00"},  # crossed
        {"bid": "38.10", "ask": "38.12", "observed_at": "2026-10-16T18:39:25"},  # naive time
        {"bid": "x", "ask": "38.12", "observed_at": "2026-10-16T18:39:25+00:00"},
        {"bid": "0", "ask": "38.12", "observed_at": "2026-10-16T18:39:25+00:00"},
    ],
)
def test_unusable_terminal_quotes_are_dropped(raw) -> None:
    assert b3_quotes.parse_quote(raw) is None


async def test_the_feed_keeps_the_terminals_own_timestamp_and_drops_what_turns_unusable() -> None:
    body = {
        "PETR4F": {"bid": "38.10", "ask": "38.12", "observed_at": "2026-10-16T18:39:25+00:00"}
    }

    async def fetch(names):
        return {"quotes": body}

    feed = b3_quotes.B3QuoteFeed(fetch, ["PETR4F"])
    await feed.refresh()
    quote = feed.quote("PETR4F")
    assert quote is not None and quote.observed_at == datetime(2026, 10, 16, 18, 39, 25, tzinfo=UTC)
    body.clear()  # the terminal stopped answering for this symbol
    await feed.refresh()
    assert feed.quote("PETR4F") is None  # no price is better than an old price


async def test_a_failing_executor_does_not_stop_the_feed() -> None:
    calls = {"n": 0}

    async def fetch(names):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("executor down")
        stamp = datetime.now(UTC).isoformat()
        return {"quotes": {"PETR4F": {"bid": "38.10", "ask": "38.12", "observed_at": stamp}}}

    feed = b3_quotes.B3QuoteFeed(fetch, ["PETR4F"], interval=0.01)
    feed.start()
    try:
        for _ in range(100):
            if feed.quote("PETR4F") is not None:
                break
            await asyncio.sleep(0.02)
    finally:
        await feed.stop()
    assert feed.failures >= 1 and feed.quote("PETR4F") is not None


def test_live_quote_reads_b3_from_the_feed_and_binance_is_untouched(monkeypatch) -> None:
    assert market_quotes.live_quote(PETR, market_source="BINANCE") is None  # no feed yet
    install_b3_quote()
    quote = market_quotes.live_quote(PETR, market_source="BINANCE")
    assert quote is not None and quote.ask == Decimal("38.12")
    assert market_quotes.live_quote("XP_B3:UNKNOWN", market_source="BINANCE") is None


async def test_the_feed_starts_only_when_xp_routing_is_configured(monkeypatch) -> None:
    configure(monkeypatch, **XP_OFF)
    assert await b3_quotes.start_if_enabled() is None
    assert b3_quotes.quote_for("PETR4F") is None

    configure(monkeypatch, **XP_ON)
    xp = FakeClient("xp")
    stamp = datetime.now(UTC).isoformat()
    xp.quote_body = {"PETR4F": {"bid": "38.10", "ask": "38.12", "observed_at": stamp}}
    wire(monkeypatch, FakeClient("binance"), xp)
    feed = await b3_quotes.start_if_enabled()
    try:
        assert feed is not None
        for _ in range(100):
            if b3_quotes.quote_for("PETR4F") is not None:
                break
            await asyncio.sleep(0.02)
        assert b3_quotes.quote_for("PETR4F") is not None
        assert ("quotes", ("BOVA11F", "PETR4F", "VALE3F")) in xp.calls
    finally:
        await b3_quotes.stop(feed)
    assert b3_quotes.quote_for("PETR4F") is None


# ---------- dispatch and reconciliation per venue ----------


async def test_each_intent_reaches_only_the_executor_of_its_venue(monkeypatch) -> None:
    configure(monkeypatch, **XP_ON)
    binance, xp = FakeClient("binance"), FakeClient("xp")
    wire(monkeypatch, binance, xp)
    to_xp = await oms_surface.execute(intent(PETR, VenueId.XP_B3), None)
    assert to_xp["client_order_id"] == "c-xp"
    assert (await oms_surface.execute(intent(BTC, VenueId.BINANCE_SPOT), None))[
        "client_order_id"
    ] == "c-binance"
    assert xp.calls == [("submit", PETR)] and binance.calls == [("submit", BTC)]


async def test_an_xp_intent_is_refused_when_xp_routing_is_not_configured(monkeypatch) -> None:
    configure(monkeypatch, **XP_OFF)
    binance, xp = FakeClient("binance"), FakeClient("xp")
    wire(monkeypatch, binance, xp)
    assert await oms_surface.execute(intent(PETR, VenueId.XP_B3), None) is None
    assert binance.calls == [] and xp.calls == []  # not even the Binance executor sees it


async def test_a_halted_xp_executor_kills_the_xp_venue_only(monkeypatch) -> None:
    configure(monkeypatch, **XP_ON)
    wire(monkeypatch, FakeClient("binance"), FakeClient("xp", halted=True))
    await oms_surface.execute(intent(PETR, VenueId.XP_B3), None)
    killed = risk_surface.governor().controls().killed_venues
    assert killed == frozenset({"XP_B3"})


async def test_a_divergence_kills_only_its_own_venue(monkeypatch) -> None:
    configure(monkeypatch, **XP_ON)
    wire(monkeypatch, FakeClient("binance"), FakeClient("xp", consistent=False))
    result = await oms_surface.reconcile(VenueId.XP_B3)
    assert result["venue"] == "XP_B3" and result["consistent"] is False
    assert risk_surface.governor().controls().killed_venues == frozenset({"XP_B3"})
    await oms_surface._halt_venue([], VenueId.BINANCE_SPOT)
    assert risk_surface.governor().controls().killed_venues == frozenset({"XP_B3", "BINANCE_SPOT"})


# ---------- the account the Risk Engine sees ----------


async def test_brl_equity_comes_from_the_xp_ledger_and_opening_holdings_stay_outside(
    monkeypatch,
) -> None:
    configure(monkeypatch, **XP_ON)
    xp = FakeClient(
        "xp",
        balances={
            "BRL": {"total": "1000000", "opening": "1000000"},
            "PETR4F": {"total": "30", "opening": "30"},
        },
    )
    binance = FakeClient("binance", balances={"USDT": {"total": "10000", "opening": "10000"}})
    wire(monkeypatch, binance, xp)
    install_b3_quote()
    state = await oms_surface.account_state("BRL")
    assert state.positions[PETR] == 0 and state.gross_exposure == 0
    assert state.equity == Decimal("1000000")
    assert ("ledger",) in xp.calls and ("ledger",) not in binance.calls
    usdt = await oms_surface.account_state("USDT")
    assert usdt.equity == Decimal("10000") and ("ledger",) in binance.calls

    xp.balances["PETR4F"] = {"total": "40", "opening": "30"}  # PROTO bought 10
    traded = await oms_surface.account_state("BRL")
    assert traded.positions[PETR] == Decimal("10")
    assert traded.gross_exposure == Decimal("381.10")  # 10 x mid 38.11


async def test_xp_equity_has_its_own_checkpoints(monkeypatch) -> None:
    configure(monkeypatch, **XP_ON)
    wire(
        monkeypatch,
        FakeClient("binance"),
        FakeClient("xp", balances={"BRL": {"total": "1000000", "opening": "1000000"}}),
    )
    await oms_surface.account_state("BRL")
    xp_store = oms_surface._checkpoint_store(VenueId.XP_B3)
    assert await xp_store.get("BRL") is not None
    assert await oms_surface._checkpoint_store().get("BRL") is None  # Binance's store untouched


async def test_a_missing_b3_quote_blocks_new_risk_instead_of_inventing_a_price(
    monkeypatch,
) -> None:
    configure(monkeypatch, **XP_ON)
    wire(
        monkeypatch,
        FakeClient("binance"),
        FakeClient(
            "xp",
            balances={
                "BRL": {"total": "1000000", "opening": "1000000"},
                "PETR4F": {"total": "40", "opening": "30"},
            },
        ),
    )
    state = await oms_surface.account_state("BRL")  # no quote installed
    assert state.gross_exposure == Decimal("Infinity")


async def test_without_xp_routing_brl_is_read_exactly_as_before(monkeypatch) -> None:
    configure(monkeypatch, **XP_OFF)
    binance, xp = FakeClient("binance"), FakeClient("xp")
    wire(monkeypatch, binance, xp)
    await oms_surface.account_state("BRL")
    assert ("ledger",) in binance.calls and xp.calls == []


# ---------- operator routes ----------


def test_routes_take_a_venue_and_refuse_xp_when_it_is_not_configured(monkeypatch) -> None:
    configure(monkeypatch, **XP_ON)
    binance, xp = FakeClient("binance"), FakeClient("xp")
    wire(monkeypatch, binance, xp)
    client = TestClient(main.app)
    default = client.get("/v1/oms/orders").json()
    assert default["orders"][0]["client_order_id"] == "o-binance"
    on_xp = client.get("/v1/oms/orders", params={"venue": "XP_B3"}).json()
    assert on_xp["orders"][0]["client_order_id"] == "o-xp" and on_xp["venue"] == "XP_B3"
    assert client.get("/v1/oms/orders", params={"venue": "PAPER"}).status_code == 422
    assert client.get("/v1/oms/orders", params={"venue": "NOPE"}).status_code == 422
    cancelled = client.post(
        "/v1/oms/orders/PXabc/cancel", params={"venue": "XP_B3"}, headers=HEADERS
    )
    assert cancelled.status_code == 200 and ("cancel", "PXabc") in xp.calls
    released = client.post(
        "/v1/oms/executor/halt/release", params={"venue": "XP_B3"}, headers=HEADERS
    )
    assert released.status_code == 200 and ("release",) in xp.calls
    assert ("release",) not in binance.calls

    configure(monkeypatch, **XP_OFF)
    assert client.get("/v1/oms/orders", params={"venue": "XP_B3"}).status_code == 409


def test_reconcile_and_ledger_views_add_the_xp_block_only_when_configured(monkeypatch) -> None:
    configure(monkeypatch, **XP_ON)
    binance = FakeClient("binance", balances={"USDT": {"total": "10000", "opening": "10000"}})
    xp = FakeClient("xp", balances={"BRL": {"total": "1000000", "opening": "1000000"}})
    wire(monkeypatch, binance, xp)
    client = TestClient(main.app)
    rec = client.post("/v1/oms/reconcile", headers=HEADERS).json()
    assert rec["venue"] == "BINANCE_SPOT" and rec["xp_b3"]["venue"] == "XP_B3"
    view = client.get("/v1/oms/ledger").json()
    assert view["xp_b3"]["account_id"] == "acct-xp" and view["venue"] == "BINANCE_SPOT"
    assert view["accounts"]["BRL"]["equity"] == "1000000"  # BRL is read from XP

    configure(monkeypatch, **XP_OFF)
    off = client.post("/v1/oms/reconcile", headers=HEADERS).json()
    assert "xp_b3" not in off
    assert "xp_b3" not in client.get("/v1/oms/ledger").json()


def test_the_agents_and_the_risk_engine_know_b3_only_when_xp_is_on(monkeypatch) -> None:
    configure(monkeypatch, **XP_OFF)
    client = TestClient(main.app)
    listed = client.get("/v1/agents/status").json()["instruments"]
    assert not any(i.startswith("XP_B3:") for i in listed)
    assert not any(i.startswith("XP_B3:") for i in risk_surface.governor().engine._instruments)

    configure(monkeypatch, **XP_ON)
    agent_surface.reset_agent_state()
    risk_surface.reset_risk_state()
    status = client.get("/v1/agents/status").json()
    assert PETR in status["instruments"]
    assert PETR in risk_surface.governor().engine._instruments
