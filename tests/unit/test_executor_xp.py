"""The executor on the XP_B3 venue: the MT5 bridge behind the same OMS, ledger and halts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fake_binance import FakeBinance, creds
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine
from test_xp_mt5 import FakeTerminal, wire

from apps.executor import runtime
from apps.executor.main import create_app
from apps.executor.runtime import Executor
from apps.executor.settings import ExecutorSettings
from apps.mt5_bridge import gateway as g
from services.execution import OrderType, TimeInForce, TradeIntent, VenueId
from services.risk import PermitSigner
from services.venues.binance import BinanceSpotAdapter, load_instruments
from services.venues.credentials import VenueEnvironment
from services.venues.xp_mt5 import XpMt5Adapter

TOKEN = "t" * 32
KEY = "p" * 32
BRIDGE_TOKEN = "b" * 40
SIGNER = PermitSigner(KEY.encode(), ttl=timedelta(seconds=60))
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PETR = "XP_B3:PETR4F"


class Account(FakeTerminal):
    """A terminal whose cash and holdings follow its fills, so the ledger can reconcile."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cash = 10_000.0
        self.held = 30.0

    def account(self):
        return {**super().account(), "balance": self.cash}

    def positions(self):
        return [{"symbol": "PETR4F", "type": g.ORDER_TYPE_BUY, "volume": self.held}]

    def fill(self, ticket, volume, price, commission=0.0):
        super().fill(ticket, volume, price, commission)
        self.cash -= volume * price
        self.held += volume


def settings(**overrides) -> ExecutorSettings:
    values = {
        "api_token": TOKEN,
        "permit_signing_key": KEY,
        "venue": VenueId.XP_B3,
        "bridge_url": "http://bridge",
        "bridge_token": BRIDGE_TOKEN,
        "symbols": "PETR4F,BOVA11F",
    }
    values.update(overrides)
    return ExecutorSettings(**values)


def factory(terminal: FakeTerminal, tmp_path):
    async def build(_settings: ExecutorSettings) -> XpMt5Adapter:
        client = wire(terminal, tmp_path)
        return await XpMt5Adapter.connect(client, symbols=["PETR4F", "BOVA11F"])

    return build


def xp_executor(terminal: FakeTerminal, tmp_path) -> Executor:
    db = f"sqlite+aiosqlite:///{tmp_path / 'executor.db'}"
    return Executor(
        settings(), adapter_factory=factory(terminal, tmp_path), engine=create_async_engine(db)
    )


def intent(**overrides) -> TradeIntent:
    values = {
        "intent_id": uuid4(),
        "instrument_id": PETR,
        "venue": VenueId.XP_B3,
        "side": "BUY",
        "quantity": Decimal("10"),
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal("38.12"),
        "time_in_force": TimeInForce.DAY,
        "strategy_id": "hybrid-fusion-v1",
        "model_version": "fusion-v1",
        "signal_ids": (uuid4(),),
        "created_at": datetime.now(UTC),
    }
    values.update(overrides)
    return TradeIntent.model_validate(values)


def permit(trade: TradeIntent):
    return SIGNER.issue(trade, now=datetime.now(UTC))


def test_settings_default_to_binance_and_keep_its_account_id() -> None:
    binance = ExecutorSettings(api_token=TOKEN, permit_signing_key=KEY)
    assert binance.venue is VenueId.BINANCE_SPOT
    assert binance.account_id == "binance-spot-testnet-main"
    assert binance.symbol_list == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert settings().account_id == "xp-b3-testnet-main"
    assert settings().symbol_list == ["PETR4F", "BOVA11F"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"bridge_url": None},
        {"bridge_token": None},
        {"symbols": None},
        {"bridge_token": TOKEN},  # must differ from the executor's own token
        {"venue": VenueId.PAPER},
    ],
)
def test_xp_settings_fail_closed_when_incomplete(overrides) -> None:
    with pytest.raises(ValidationError):
        settings(**overrides)


def test_the_default_adapter_factory_follows_the_venue() -> None:
    binance = ExecutorSettings(api_token=TOKEN, permit_signing_key=KEY)
    assert runtime.default_adapter_factory(binance) is runtime.binance_adapter
    assert runtime.default_adapter_factory(settings()) is runtime.xp_adapter


async def test_xp_adapter_reads_the_account_through_the_bridge_client(
    tmp_path, monkeypatch
) -> None:
    seen = {}

    def bridge(url: str, token: str):
        seen.update(url=url, token=token)
        return wire(Account(), tmp_path)

    monkeypatch.setattr(runtime, "bridge_client", bridge)
    adapter = await runtime.xp_adapter(settings())
    try:
        assert seen == {"url": "http://bridge", "token": BRIDGE_TOKEN}
        assert adapter.venue is VenueId.XP_B3 and adapter.real_money_execution is False
        assert set(adapter.instruments) == {"XP_B3:PETR4F", "XP_B3:BOVA11F"}
    finally:
        await adapter.aclose()


async def test_the_ledger_opens_from_the_terminal_and_an_xp_order_fills(
    tmp_path, monkeypatch
) -> None:
    # SQLite keeps the ledger's NUMERIC amounts as float (production runs Postgres, which is
    # exact), so use values a float holds exactly: no fee reserve and a round price.
    monkeypatch.setattr("services.oms.manager.FEE_RESERVE", Decimal("0"))
    terminal = Account()
    executor = xp_executor(terminal, tmp_path)
    await executor.start()
    try:
        assert executor.venue is VenueId.XP_B3
        opening = await executor.ledger.balances()
        assert opening["BRL"].total == Decimal("10000")
        assert opening["PETR4F"].total == Decimal("30")

        trade = intent(limit_price=Decimal("38.00"))
        order = await executor.submit(trade, permit(trade))
        assert order.status.value == "ACKNOWLEDGED"  # resting at the terminal

        terminal.fill(1000, 10.0, 38.00)
        await executor.poll_open_orders()
        filled = executor.oms.get(order.client_order_id)
        assert filled.status.value == "FILLED"
        balances = await executor.ledger.balances()
        assert balances["PETR4F"].total == Decimal("40")
        assert balances["BRL"].total == Decimal("9620")

        result = await executor.reconcile()
        assert result["venue"] == "XP_B3" and result["consistent"] is True
        assert executor.halted is None
    finally:
        await executor.stop()


async def test_an_xp_executor_refuses_orders_for_another_venue(tmp_path) -> None:
    executor = xp_executor(Account(), tmp_path)
    await executor.start()
    try:
        other = intent(
            instrument_id="BINANCE_SPOT:BTCUSDT",
            venue=VenueId.BINANCE_SPOT,
            quantity=Decimal("0.01"),
            limit_price=Decimal("62100.00"),
            time_in_force=TimeInForce.IOC,
        )
        with pytest.raises(ValueError, match="only trades XP_B3"):
            await executor.submit(other, permit(other))
    finally:
        await executor.stop()


async def test_a_real_account_is_refused_at_start(tmp_path) -> None:
    executor = xp_executor(Account(mode=g.ACCOUNT_TRADE_MODE_REAL), tmp_path)
    with pytest.raises(RuntimeError, match="real-money adapters are refused"):
        await executor.start()


async def test_a_divergence_halts_the_xp_executor(tmp_path) -> None:
    terminal = Account()
    executor = xp_executor(terminal, tmp_path)
    await executor.start()
    try:
        terminal.cash -= 1.0  # money moved outside the system
        result = await executor.reconcile()
        assert result["consistent"] is False and executor.halted is not None
        trade = intent()
        with pytest.raises(Exception, match="reconciliation divergence"):
            await executor.submit(trade, permit(trade))
    finally:
        await executor.stop()


def test_quotes_route_serves_the_terminal_quotes_and_requires_names(tmp_path) -> None:
    with TestClient(create_app(xp_executor(Account(), tmp_path))) as client:
        assert client.get("/v1/quotes", params={"names": "PETR4F"}).status_code == 401
        quotes = client.get("/v1/quotes", params={"names": "PETR4F"}, headers=AUTH).json()
        assert Decimal(quotes["quotes"]["PETR4F"]["bid"]) == Decimal("38.10")
        assert quotes["real_money_execution"] is False
        assert client.get("/v1/quotes", headers=AUTH).status_code == 422
        ledger = client.get("/v1/ledger", headers=AUTH).json()
        assert ledger["account_id"] == "xp-b3-testnet-main"


def test_a_venue_without_a_quote_feed_answers_404(tmp_path) -> None:
    fake = FakeBinance()
    fake.server_time_ms = int(datetime.now(UTC).timestamp() * 1000)
    fake.time_tolerance_ms = 10_000

    async def build(_settings: ExecutorSettings) -> BinanceSpotAdapter:
        client = httpx.AsyncClient(transport=fake.transport())
        instruments = await load_instruments(
            ["BTCUSDT"], environment=VenueEnvironment.TESTNET, client=client
        )
        return BinanceSpotAdapter(
            creds(VenueEnvironment.TESTNET), instruments=instruments, client=client
        )

    binance = Executor(
        ExecutorSettings(api_token=TOKEN, permit_signing_key=KEY, symbols="BTCUSDT"),
        adapter_factory=build,
        engine=create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}"),
    )
    with TestClient(create_app(binance)) as client:
        response = client.get("/v1/quotes", params={"names": "BTCUSDT"}, headers=AUTH)
        assert response.status_code == 404
