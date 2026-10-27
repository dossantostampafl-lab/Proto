"""Alpaca US equities, PAPER only: pinned host, market clock, orders, balances, IEX quotes.

Offline: a fake Alpaca behind httpx.MockTransport. Nothing here reaches the network.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from apps.executor import runtime
from apps.executor.main import create_app
from apps.executor.runtime import Executor
from apps.executor.settings import ExecutorSettings
from services.execution import OrderStatus, OrderType, TimeInForce, TradeIntent, VenueId
from services.execution.venue import VenueOrder
from services.risk import PermitSigner
from services.venues import alpaca
from services.venues.alpaca import AlpacaPaperAdapter, parse_latest_quote
from services.venues.binance import VenueOutcomeUnknown
from services.venues.credentials import VenueCredentials, VenueEnvironment, load_credentials

KEY_ID, SECRET = "PKTESTKEYID000000000", "s" * 40
SPY, QQQ = "ALPACA_US:SPY", "ALPACA_US:QQQ"
TOKEN, PERMIT_KEY = "t" * 32, "p" * 32
SIGNER = PermitSigner(PERMIT_KEY.encode(), ttl=timedelta(seconds=60))
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def creds(environment: VenueEnvironment = VenueEnvironment.TESTNET) -> VenueCredentials:
    return VenueCredentials(
        venue=VenueId.ALPACA_US,
        environment=environment,
        account="main",
        api_key=KEY_ID,
        api_secret=SECRET,
    )


class FakeAlpaca:
    """Paper account whose cash and positions follow its fills, so the ledger can reconcile."""

    def __init__(self) -> None:
        self.open = True
        self.clock_down = False
        self.post_error: int | None = None
        self.redirect = False
        self.cash = Decimal("10000")
        self.positions: dict[str, Decimal] = {"QQQ": Decimal("2")}
        self.ask = Decimal("500")
        self.orders: dict[str, dict] = {}
        self.hosts: set[str] = set()
        self.posts: list[dict] = []
        self.assets = {
            "SPY": {"tradable": True, "status": "active", "class": "us_equity"},
            "QQQ": {"tradable": True, "status": "active", "class": "us_equity"},
            "HALT": {"tradable": False, "status": "active", "class": "us_equity"},
        }
        self.quotes = {
            "SPY": {"bp": 499.98, "ap": 500.0, "t": "2026-10-27T14:31:02.123456789Z"},
            "QQQ": {"bp": 0, "ap": 0, "t": "2026-10-27T14:31:02Z"},  # no IEX quote now
        }

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.hosts.add(request.url.host)
        assert request.headers["APCA-API-KEY-ID"] == KEY_ID
        assert request.headers["APCA-API-SECRET-KEY"] == SECRET
        path, method = request.url.path, request.method
        if request.url.host == "data.alpaca.markets":
            assert request.url.params["feed"] == "iex"
            if path == "/v2/stocks/quotes/latest":
                return httpx.Response(200, json={"quotes": self.quotes})
            if path == "/v2/stocks/bars/latest":
                bar = {"t": "2026-10-27T14:31:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 9}
                return httpx.Response(200, json={"bars": {"SPY": bar}})
            return httpx.Response(404, json={"message": "not found"})
        assert request.url.host == "paper-api.alpaca.markets"
        if self.redirect:
            return httpx.Response(302, headers={"Location": "https://api.alpaca.markets/v2/x"})
        if path == "/v2/clock":
            if self.clock_down:
                return httpx.Response(503)
            return httpx.Response(200, json={"is_open": self.open})
        if path == "/v2/account":
            return httpx.Response(
                200,
                json={
                    "status": "ACTIVE",
                    "currency": "USD",
                    "cash": str(self.cash),
                    "trading_blocked": False,
                },
            )
        if path == "/v2/positions":
            rows = [{"symbol": s, "qty": str(q)} for s, q in self.positions.items() if q]
            return httpx.Response(200, json=rows)
        if path.startswith("/v2/assets/"):
            asset = self.assets.get(path.rsplit("/", 1)[1])
            if asset is None:
                return httpx.Response(404, json={"message": "asset not found"})
            return httpx.Response(200, json={"symbol": path.rsplit("/", 1)[1], **asset})
        if path == "/v2/orders" and method == "POST":
            if self.post_error:
                return httpx.Response(self.post_error)
            body = json.loads(request.content)
            self.posts.append(body)
            if body["client_order_id"] in self.orders:
                return httpx.Response(422, json={"message": "client_order_id must be unique"})
            return httpx.Response(200, json=self._place(body))
        if path == "/v2/orders:by_client_order_id":
            order = self.orders.get(request.url.params["client_order_id"])
            if order is None:
                return httpx.Response(404, json={"message": "order not found"})
            return httpx.Response(200, json=order)
        if path.startswith("/v2/orders/") and method == "DELETE":
            order_id = path.rsplit("/", 1)[1]
            order = next((o for o in self.orders.values() if o["id"] == order_id), None)
            if order is None:
                return httpx.Response(404, json={"message": "order not found"})
            if order["status"] not in ("new", "accepted"):
                return httpx.Response(422, json={"message": "order is not cancelable"})
            order["status"] = "canceled"
            return httpx.Response(204)
        return httpx.Response(404, json={"message": f"no route {method} {path}"})

    def _place(self, body: dict) -> dict:
        qty, limit = Decimal(body["qty"]), Decimal(body["limit_price"])
        buying = body["side"] == "buy"
        marketable = limit >= self.ask if buying else limit <= self.ask
        order = {
            "id": str(uuid4()),
            "client_order_id": body["client_order_id"],
            "symbol": body["symbol"],
            "side": body["side"],
            "qty": body["qty"],
            "filled_qty": "0",
            "filled_avg_price": None,
            "status": "new",
            "updated_at": "2026-10-27T14:31:03.5Z",
        }
        if marketable:
            self.fill(order, qty, self.ask)
        elif body["time_in_force"] == "ioc":
            order["status"] = "canceled"
        self.orders[body["client_order_id"]] = order
        return order

    def fill(self, order: dict, qty: Decimal, price: Decimal) -> None:
        sign = 1 if order["side"] == "buy" else -1
        self.cash -= sign * qty * price
        self.positions[order["symbol"]] = self.positions.get(order["symbol"], Decimal("0")) + (
            sign * qty
        )
        order.update(filled_qty=str(qty), filled_avg_price=str(price), status="filled")


async def adapter(fake: FakeAlpaca, symbols=("SPY", "QQQ")) -> AlpacaPaperAdapter:
    client = httpx.AsyncClient(transport=fake.transport())
    return await AlpacaPaperAdapter.connect(creds(), symbols=symbols, client=client)


def order(**overrides) -> VenueOrder:
    values = {
        "client_order_id": "PX" + "a" * 30,
        "instrument_id": SPY,
        "side": "BUY",
        "quantity": Decimal("1"),
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal("500.00"),
        "time_in_force": TimeInForce.IOC,
    }
    values.update(overrides)
    return VenueOrder(**values)


# ---------- paper only ----------


def test_no_code_path_names_the_live_trading_host() -> None:
    source = Path(alpaca.__file__).read_text()
    hosts = set(re.findall(r"https?://([a-z0-9.-]+)", source))
    assert hosts == {"paper-api.alpaca.markets", "data.alpaca.markets"}
    assert alpaca.PAPER_TRADING_URL == "https://paper-api.alpaca.markets"
    # No setting can point it elsewhere: the executor has no Alpaca URL setting at all.
    assert not any("alpaca" in name or "url" == name for name in ExecutorSettings.model_fields)
    with pytest.raises(RuntimeError):
        alpaca._trading_url("@api.alpaca.markets/v2/orders")


def test_live_credentials_are_refused_and_the_adapter_never_reports_real_money() -> None:
    with pytest.raises(ValueError, match="paper account only"):
        AlpacaPaperAdapter(creds(VenueEnvironment.LIVE), instruments={})
    paper = AlpacaPaperAdapter(creds(), instruments={})
    assert paper.real_money_execution is False
    assert paper.environment is VenueEnvironment.TESTNET
    assert SECRET not in repr(paper) and KEY_ID not in repr(paper)
    assert SECRET not in str(paper._credentials)


async def test_a_full_lifecycle_touches_only_the_paper_and_data_hosts() -> None:
    fake = FakeAlpaca()
    venue = await adapter(fake)
    await venue.submit(order())
    await venue.query(order().client_order_id)
    await venue.balances()
    await venue.quotes(["SPY"])
    await venue.latest_bars(["SPY"])
    await venue.aclose()
    assert fake.hosts == {"paper-api.alpaca.markets", "data.alpaca.markets"}


async def test_a_redirect_is_never_followed() -> None:
    fake = FakeAlpaca()
    venue = await adapter(fake)
    fake.redirect = True
    with pytest.raises(VenueOutcomeUnknown):
        await venue.balances()
    assert "api.alpaca.markets" not in fake.hosts


def test_credentials_come_from_the_shared_loader() -> None:
    env = {
        "PROTO_CRED_ALPACA_US_TESTNET_MAIN_API_KEY": KEY_ID,
        "PROTO_CRED_ALPACA_US_TESTNET_MAIN_API_SECRET": SECRET,
    }
    loaded = load_credentials(VenueId.ALPACA_US, VenueEnvironment.TESTNET, "main", env=env)
    assert loaded.api_key == KEY_ID and loaded.venue is VenueId.ALPACA_US


# ---------- market clock (fail closed) ----------


@pytest.mark.parametrize("closed", ["closed", "clock_down"])
async def test_no_order_is_sent_while_the_market_is_closed_or_the_clock_unreadable(closed) -> None:
    fake = FakeAlpaca()
    venue = await adapter(fake)
    if closed == "closed":
        fake.open = False
    else:
        fake.clock_down = True
    report = await venue.submit(order())
    assert report.status is OrderStatus.REJECTED and "closed" in (report.reason or "")
    assert fake.posts == []


# ---------- orders ----------


async def test_a_marketable_ioc_fills_and_a_resubmit_returns_the_same_order() -> None:
    fake = FakeAlpaca()
    venue = await adapter(fake)
    first = await venue.submit(order())
    assert first.status is OrderStatus.FILLED and first.average_price == Decimal("500")
    assert first.fee == 0 and first.venue is VenueId.ALPACA_US
    assert fake.posts[0] == {
        "symbol": "SPY",
        "qty": "1",
        "side": "buy",
        "type": "limit",
        "time_in_force": "ioc",
        "limit_price": "500",
        "client_order_id": "PX" + "a" * 30,
    }
    again = await venue.submit(order())  # Alpaca refuses the duplicate id: report what it holds
    assert again.status is OrderStatus.FILLED and again.venue_order_id == first.venue_order_id
    assert len(fake.orders) == 1
    queried = await venue.query(order().client_order_id)
    assert queried is not None and queried.filled_quantity == Decimal("1")
    assert await venue.query("PX-unknown") is None


async def test_a_day_order_rests_then_cancels_and_an_unmarketable_ioc_expires() -> None:
    fake = FakeAlpaca()
    venue = await adapter(fake)
    resting = await venue.submit(order(limit_price=Decimal("400"), time_in_force=TimeInForce.DAY))
    assert resting.status is OrderStatus.ACKNOWLEDGED
    canceled = await venue.cancel(resting.client_order_id)
    assert canceled.status is OrderStatus.CANCELED
    again = await venue.cancel(resting.client_order_id)  # not cancelable any more: the truth
    assert again.status is OrderStatus.CANCELED
    expired = await venue.submit(order(client_order_id="PX" + "b" * 30, limit_price=Decimal("400")))
    assert expired.status is OrderStatus.CANCELED
    with pytest.raises(LookupError):
        await venue.cancel("PX-none")


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"time_in_force": TimeInForce.GTC}, "IOC or DAY"),
        ({"order_type": OrderType.MARKET, "limit_price": None}, "LIMIT"),
        ({"quantity": Decimal("0.5")}, "whole shares"),
        ({"instrument_id": "ALPACA_US:HALT"}, "not loaded"),
    ],
)
async def test_orders_outside_the_contract_are_rejected_without_being_sent(
    overrides, reason
) -> None:
    fake = FakeAlpaca()
    venue = await adapter(fake, symbols=("SPY", "QQQ", "HALT"))
    report = await venue.submit(order(**overrides))
    assert report.status is OrderStatus.REJECTED and reason in (report.reason or "")
    assert fake.posts == []


@pytest.mark.parametrize("failure", [500, "timeout"])
async def test_an_unanswered_submit_is_an_unknown_outcome(failure) -> None:
    fake = FakeAlpaca()
    venue = await adapter(fake)
    if failure == "timeout":

        def boom(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                raise httpx.ReadTimeout("slow", request=request)
            return fake.handler(request)

        venue._client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    else:
        fake.post_error = failure
    with pytest.raises(VenueOutcomeUnknown):
        await venue.submit(order())


async def test_a_definite_refusal_is_a_rejection() -> None:
    fake = FakeAlpaca()
    venue = await adapter(fake)

    def refuse(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(403, json={"message": "insufficient buying power"})
        return fake.handler(request)

    venue._client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    report = await venue.submit(order())
    assert report.status is OrderStatus.REJECTED and "buying power" in (report.reason or "")


# ---------- account, assets, quotes ----------


async def test_balances_are_usd_cash_plus_positions() -> None:
    fake = FakeAlpaca()
    venue = await adapter(fake)
    assert await venue.balances() == {"USD": Decimal("10000"), "QQQ": Decimal("2")}


async def test_only_tradable_assets_load_with_whole_share_grids() -> None:
    fake = FakeAlpaca()
    venue = await adapter(fake, symbols=("SPY", "HALT", "NOPE"))
    assert set(venue.instruments) == {SPY}
    spy = venue.instruments[SPY]
    assert spy.lot_size == 1 and spy.quote_asset == "USD" and spy.timezone == "America/New_York"


async def test_an_inactive_account_is_refused_at_connect() -> None:
    def blocked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ACCOUNT_CLOSED", "currency": "USD"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(blocked))
    with pytest.raises(RuntimeError, match="not active"):
        await AlpacaPaperAdapter.connect(creds(), symbols=["SPY"], client=client)


async def test_quotes_are_iex_labelled_with_alpacas_timestamp_and_one_sided_ones_dropped() -> None:
    fake = FakeAlpaca()
    venue = await adapter(fake)
    quotes = await venue.quotes(["SPY", "QQQ"])
    assert set(quotes) == {"SPY"}  # QQQ has no two-sided IEX quote: left out
    spy = quotes["SPY"]
    assert spy["feed"] == "IEX"
    assert Decimal(spy["bid"]) == Decimal("499.98") and Decimal(spy["ask"]) == Decimal("500.0")
    assert datetime.fromisoformat(spy["observed_at"]) == datetime(
        2026, 10, 27, 14, 31, 2, 123456, tzinfo=UTC
    )
    bars = await venue.latest_bars(["SPY"])
    assert bars["SPY"]["feed"] == "IEX" and Decimal(bars["SPY"]["c"]) == Decimal("1.5")


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"bp": 501, "ap": 500, "t": "2026-10-27T14:31:02Z"},  # crossed
        {"bp": 499, "ap": 500},  # no timestamp
        {"bp": 499, "ap": 500, "t": "2026-10-27T14:31:02"},  # naive
        {"bp": "x", "ap": 500, "t": "2026-10-27T14:31:02Z"},
    ],
)
def test_unusable_quotes_are_dropped(raw) -> None:
    assert parse_latest_quote(raw) is None


# ---------- the executor on ALPACA_US ----------


def settings(**overrides) -> ExecutorSettings:
    values = {
        "api_token": TOKEN,
        "permit_signing_key": PERMIT_KEY,
        "venue": VenueId.ALPACA_US,
        "symbols": "SPY,QQQ",
    }
    values.update(overrides)
    return ExecutorSettings(**values)


def test_executor_settings_accept_alpaca_and_require_symbols() -> None:
    configured = settings()
    assert configured.account_id == "alpaca-us-testnet-main"
    assert configured.symbol_list == ["SPY", "QQQ"]
    assert runtime.default_adapter_factory(configured) is runtime.alpaca_adapter
    with pytest.raises(ValidationError, match="EXECUTOR_SYMBOLS"):
        settings(symbols=None)
    with pytest.raises(ValidationError, match="XP_B3"):
        settings(bridge_url="http://bridge", bridge_token="b" * 40)
    with pytest.raises(ValidationError, match="testnet only"):
        settings(environment=VenueEnvironment.LIVE)


def alpaca_executor(fake: FakeAlpaca, tmp_path) -> Executor:
    async def build(_settings: ExecutorSettings) -> AlpacaPaperAdapter:
        return await adapter(fake)

    return Executor(
        settings(),
        adapter_factory=build,
        engine=create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'alpaca.db'}"),
    )


def intent(**overrides) -> TradeIntent:
    values = {
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
    values.update(overrides)
    return TradeIntent.model_validate(values)


async def test_the_executor_fills_reconciles_and_stays_idempotent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("services.oms.manager.FEE_RESERVE", Decimal("0"))
    fake = FakeAlpaca()
    executor = alpaca_executor(fake, tmp_path)
    await executor.start()
    try:
        opening = await executor.ledger.balances()
        assert opening["USD"].total == Decimal("10000") and opening["QQQ"].total == Decimal("2")
        trade = intent()
        order_ = await executor.submit(trade, SIGNER.issue(trade, now=datetime.now(UTC)))
        assert order_.status is OrderStatus.FILLED
        again = await executor.submit(trade, SIGNER.issue(trade, now=datetime.now(UTC)))
        assert again.client_order_id == order_.client_order_id and len(fake.orders) == 1
        balances = await executor.ledger.balances()
        assert balances["SPY"].total == Decimal("1") and balances["USD"].total == Decimal("9500")
        result = await executor.reconcile()
        assert result["venue"] == "ALPACA_US" and result["consistent"] is True

        fake.cash -= Decimal("1")  # money moved outside the system
        assert (await executor.reconcile())["consistent"] is False
        assert executor.halted == "reconciliation divergence"
    finally:
        await executor.stop()


async def test_a_closed_market_rejects_through_the_executor(tmp_path) -> None:
    fake = FakeAlpaca()
    fake.open = False
    executor = alpaca_executor(fake, tmp_path)
    await executor.start()
    try:
        trade = intent()
        order_ = await executor.submit(trade, SIGNER.issue(trade, now=datetime.now(UTC)))
        assert order_.status is OrderStatus.REJECTED and fake.posts == []
        other = intent(instrument_id="XP_B3:PETR4F", venue=VenueId.XP_B3)
        with pytest.raises(ValueError, match="only trades ALPACA_US"):
            await executor.submit(other, SIGNER.issue(other, now=datetime.now(UTC)))
    finally:
        await executor.stop()


def test_the_executor_serves_iex_quotes_for_configured_symbols_only(tmp_path) -> None:
    with TestClient(create_app(alpaca_executor(FakeAlpaca(), tmp_path))) as client:
        body = client.get("/v1/quotes", params={"names": "SPY"}, headers=AUTH).json()
        assert body["quotes"]["SPY"]["feed"] == "IEX"
        assert body["real_money_execution"] is False and body["financial_connectivity"] is False
        outside = client.get("/v1/quotes", params={"names": "SPY,TSLA"}, headers=AUTH)
        assert outside.status_code == 422
        health = client.get("/health").json()
        assert health["venue_environment"] == "TESTNET" and health["real_money_execution"] is False
