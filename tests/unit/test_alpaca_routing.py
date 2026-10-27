"""API side of Alpaca US (paper): off by default, routed like XP, USD limits via FX, US hours."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_xp_routing import FakeClient

from apps.api.app import (
    agent_surface,
    b3_quotes,
    fx_rates,
    instrument_registry,
    main,
    market_quotes,
    oms_surface,
    risk_surface,
    roster_surface,
    signal_surface,
    strategy_surface,
    us_quotes,
)
from apps.api.app import settings as settings_module
from apps.api.app.settings import Settings
from services.agents.roster import INITIAL_SLOTS, US_SLOTS, AgentRoster
from services.execution import OrderType, TimeInForce, TradeIntent, VenueId
from services.execution.catalog import PAPER_INSTRUMENTS, US_INSTRUMENTS
from services.execution.constitution import REFERENCE_CAPITAL, reference_capital_in
from services.execution.venue import Quote
from services.risk import session_open

OPERATOR = "o" * 32
BINANCE_TOKEN = "e" * 32
ALPACA_TOKEN = "a" * 32
SPY = "ALPACA_US:SPY"
ALPACA_ON = {
    "oms_execution_backend": "executor",
    "executor_url": "http://binance-executor:8100",
    "executor_api_token": BINANCE_TOKEN,
    "alpaca_executor_url": "http://alpaca-executor:8101",
    "alpaca_executor_api_token": ALPACA_TOKEN,
    "risk_permit_signing_key": "r" * 32,
}
ALPACA_OFF = {k: v for k, v in ALPACA_ON.items() if not k.startswith("alpaca_")}


def configure(monkeypatch, **overrides) -> Settings:
    values = {"operator_api_token": OPERATOR, "live_market_source": "BINANCE", **overrides}
    configured = Settings(**values)
    for module in (
        main,
        signal_surface,
        agent_surface,
        risk_surface,
        oms_surface,
        instrument_registry,
        roster_surface,
        strategy_surface,
        settings_module,
    ):
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
        us_quotes.install(None)

    reset()
    yield
    reset()


def wire(monkeypatch, binance: FakeClient, alpaca: FakeClient) -> None:
    clients = {VenueId.BINANCE_SPOT: binance, VenueId.ALPACA_US: alpaca}
    monkeypatch.setattr(
        oms_surface, "executor_client", lambda venue=VenueId.BINANCE_SPOT: clients[venue]
    )


def intent() -> TradeIntent:
    return TradeIntent.model_validate(
        {
            "intent_id": uuid4(),
            "instrument_id": SPY,
            "venue": VenueId.ALPACA_US,
            "side": "BUY",
            "quantity": Decimal("1"),
            "order_type": OrderType.LIMIT,
            "limit_price": Decimal("500.00"),
            "time_in_force": TimeInForce.IOC,
            "strategy_id": "hybrid-fusion-v1",
            "model_version": "fusion-v1",
            "signal_ids": (uuid4(),),
            "created_at": datetime.now(UTC),
        }
    )


# ---------- off by default ----------


def test_by_default_the_api_knows_nothing_about_us_equities(monkeypatch) -> None:
    configured = configure(monkeypatch, **ALPACA_OFF)
    assert configured.alpaca_routing_enabled is False and configured.agent_us_enabled is False
    assert Settings().alpaca_executor_url is None and Settings().alpaca_executor_api_token is None
    assert instrument_registry.active_instruments() is PAPER_INSTRUMENTS
    assert oms_surface.venue_for_quote("USD") is VenueId.BINANCE_SPOT
    with pytest.raises(HTTPException) as refused:
        oms_surface._venue_param("ALPACA_US")
    assert refused.value.status_code == 409


async def test_an_alpaca_intent_is_refused_when_routing_is_off(monkeypatch) -> None:
    configure(monkeypatch, **ALPACA_OFF)
    binance, alpaca = FakeClient("binance"), FakeClient("alpaca")
    wire(monkeypatch, binance, alpaca)
    assert await oms_surface.execute(intent(), None) is None
    assert binance.calls == [] and alpaca.calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"oms_execution_backend": "paper"},
        {"alpaca_executor_api_token": None},
        {"alpaca_executor_url": None},
        {"alpaca_executor_api_token": "short"},
        {"alpaca_executor_api_token": BINANCE_TOKEN},
        {"alpaca_executor_api_token": OPERATOR},
    ],
)
def test_alpaca_settings_fail_closed_when_incomplete_or_reused(overrides) -> None:
    with pytest.raises(ValidationError):
        Settings(operator_api_token=OPERATOR, **{**ALPACA_ON, **overrides})


def test_us_quotes_view_says_disabled_by_default(monkeypatch) -> None:
    configure(monkeypatch, **ALPACA_OFF)
    with TestClient(main.app) as client:
        body = client.get("/v1/us/quotes").json()
    assert body["enabled"] is False and body["real_money_execution"] is False


# ---------- on ----------


async def test_with_alpaca_on_us_intents_reach_only_the_alpaca_executor(monkeypatch) -> None:
    configure(monkeypatch, **ALPACA_ON)
    assert set(US_INSTRUMENTS) <= set(instrument_registry.active_instruments())
    assert oms_surface.venue_for_quote("USD") is VenueId.ALPACA_US
    assert oms_surface.venue_for_quote("USDT") is VenueId.BINANCE_SPOT
    assert oms_surface._venue_param("alpaca_us") is VenueId.ALPACA_US
    binance, alpaca = FakeClient("binance"), FakeClient("alpaca", halted=True)
    wire(monkeypatch, binance, alpaca)
    placed = await oms_surface.execute(intent(), None)
    assert placed["client_order_id"] == "c-alpaca"
    assert alpaca.calls == [("submit", SPY)] and binance.calls == []
    # A halted Alpaca executor kills its own venue only.
    assert risk_surface.governor().controls().killed_venues == frozenset({"ALPACA_US"})


async def test_reconcile_and_ledger_report_alpaca_on_its_own(monkeypatch) -> None:
    configure(monkeypatch, **ALPACA_ON)
    binance = FakeClient("binance", balances={"USDT": {"total": "100", "opening": "0"}})
    alpaca = FakeClient("alpaca", down=True)
    wire(monkeypatch, binance, alpaca)
    result = await oms_surface.reconcile_now()
    assert result["consistent"] is True and "xp_b3" not in result
    assert result["alpaca_us"]["unavailable"] is True
    view = await oms_surface.ledger_view()
    assert view["alpaca_us"] == {"venue": "ALPACA_US", "unavailable": True}


async def test_a_us_quote_comes_from_the_feed_with_alpacas_timestamp(monkeypatch) -> None:
    assert market_quotes.live_quote(SPY, market_source="BINANCE") is None
    feed = b3_quotes.B3QuoteFeed(fetch=None, symbols=["SPY"], name="us")
    at = datetime.now(UTC) - timedelta(seconds=1)
    feed._quotes["SPY"] = Quote(bid=Decimal("499.98"), ask=Decimal("500"), observed_at=at)
    us_quotes.install(feed)
    quote = market_quotes.live_quote(SPY, market_source="BINANCE")
    assert quote is not None and quote.observed_at == at
    configure(monkeypatch, **ALPACA_ON)
    body = await us_quotes.quotes_view()
    assert body["feed"] == "IEX" and body["consolidated"] is False
    assert "not consolidated" in body["source"] and body["symbols"]["SPY"]["ask"] == "500"


# ---------- limits in BRL, converted to USD ----------


class Ticker:
    def __init__(self, bid: str | None, ask: str | None) -> None:
        self.bid, self.ask = bid, ask

    def client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["symbol"] == "USDTBRL"
            if self.ask is None:
                return httpx.Response(503)
            return httpx.Response(200, json={"bidPrice": self.bid, "askPrice": self.ask})

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_usd_uses_the_usdtbrl_ask_raised_by_a_margin_so_limits_only_tighten() -> None:
    rates = fx_rates.BrlRates(client=Ticker("5.40", "5.50").client())
    usd = await rates.brl_per("USD")
    assert usd == Decimal("5.50") * fx_rates.USD_PROXY_MARGIN
    assert await rates.brl_per("USDT") == Decimal("5.50")
    capital = reference_capital_in(usd)
    assert capital < REFERENCE_CAPITAL / Decimal("5.50") < REFERENCE_CAPITAL


async def test_no_fx_rate_means_no_usd_capital(monkeypatch, real_reference_capital) -> None:
    rates = fx_rates.BrlRates(client=Ticker(None, None).client())
    assert await rates.brl_per("USD") is None
    monkeypatch.setattr(fx_rates, "_rates", rates)
    assert await oms_surface._reference_capital("USD") == Decimal("0")  # sizes nothing new


# ---------- US hours ----------


def test_us_orders_only_in_the_new_york_regular_session() -> None:
    spy = US_INSTRUMENTS[SPY]
    # Tuesday 2026-10-27, New York is UTC-4 until 2026-11-01.
    assert session_open(spy, datetime(2026, 10, 27, 13, 30, tzinfo=UTC))  # 09:30
    assert session_open(spy, datetime(2026, 10, 27, 19, 59, tzinfo=UTC))  # 15:59
    assert not session_open(spy, datetime(2026, 10, 27, 13, 29, tzinfo=UTC))  # pre-market
    assert not session_open(spy, datetime(2026, 10, 27, 20, 0, tzinfo=UTC))  # 16:00
    assert not session_open(spy, datetime(2026, 10, 31, 15, 0, tzinfo=UTC))  # Saturday


# ---------- roster ----------


def test_us_agents_need_the_flag_and_the_alpaca_executor(monkeypatch) -> None:
    configure(monkeypatch)
    assert roster_surface.roster_slots() == INITIAL_SLOTS
    configure(monkeypatch, agent_us_enabled=True)  # flag without the executor: nothing changes
    assert roster_surface.roster_slots() == INITIAL_SLOTS
    configure(monkeypatch, **ALPACA_ON)  # executor without the flag: nothing changes
    assert roster_surface.roster_slots() == INITIAL_SLOTS
    assert strategy_surface.instruments() == strategy_surface._INSTRUMENTS

    configure(monkeypatch, agent_us_enabled=True, **ALPACA_ON)
    assert roster_surface.roster_slots() == INITIAL_SLOTS + US_SLOTS
    assert strategy_surface.instruments()[-2:] == ("ALPACA_US:SPY", "ALPACA_US:QQQ")


async def test_us_agents_get_r200_each_and_unique_codes() -> None:
    assert all(slot.instrument_id in US_INSTRUMENTS for slot in US_SLOTS)
    roster = AgentRoster(slots=INITIAL_SLOTS + US_SLOTS)
    await roster.seed()
    agents = roster.agents()
    assert len({a.agent_id for a in agents}) == len(agents) == 8
    us = [roster.active_for(slot) for slot in US_SLOTS]
    assert all(a is not None and a.capital_brl == Decimal("200") for a in us)
