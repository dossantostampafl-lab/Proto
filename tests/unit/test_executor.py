"""Isolated executor over the Binance fake: permits, halts, restarts, resting fills."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fake_binance import BTC_ID, FakeBinance, creds
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from apps.executor.main import create_app
from apps.executor.runtime import Executor
from apps.executor.settings import ExecutorSettings
from services.execution import OrderType, TimeInForce, TradeIntent, VenueId
from services.risk import PermitSigner
from services.venues.binance import BinanceSpotAdapter, load_instruments
from services.venues.credentials import VenueEnvironment

TOKEN = "t" * 32
KEY = "p" * 32
SIGNER = PermitSigner(KEY.encode(), ttl=timedelta(seconds=60))
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def settings(**overrides) -> ExecutorSettings:
    values = {"api_token": TOKEN, "permit_signing_key": KEY, "symbols": "BTCUSDT"}
    values.update(overrides)
    return ExecutorSettings(**values)


def factory(fake: FakeBinance, environment=VenueEnvironment.TESTNET):
    async def build(_settings: ExecutorSettings) -> BinanceSpotAdapter:
        client = httpx.AsyncClient(transport=fake.transport())
        instruments = await load_instruments(["BTCUSDT"], environment=environment, client=client)
        # Real clock: permits are checked against now.
        return BinanceSpotAdapter(creds(environment), instruments=instruments, client=client)

    return build


@pytest.fixture
def fake() -> FakeBinance:
    fake = FakeBinance()
    fake.server_time_ms = int(datetime.now(UTC).timestamp() * 1000)
    fake.time_tolerance_ms = 10_000
    return fake


@pytest.fixture
def db(tmp_path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'executor.db'}"


def executor(fake: FakeBinance, db: str) -> Executor:
    return Executor(settings(), adapter_factory=factory(fake), engine=create_async_engine(db))


def intent(**overrides) -> TradeIntent:
    values = {
        "intent_id": uuid4(),
        "instrument_id": BTC_ID,
        "venue": VenueId.BINANCE_SPOT,
        "side": "BUY",
        "quantity": Decimal("0.01"),
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal("62100.00"),
        "time_in_force": TimeInForce.IOC,
        "strategy_id": "hybrid-fusion-v1",
        "model_version": "fusion-v1",
        "signal_ids": (uuid4(),),
        "created_at": datetime.now(UTC),
    }
    values.update(overrides)
    return TradeIntent.model_validate(values)


def body(trade: TradeIntent, permit=None) -> dict:
    permit = permit or SIGNER.issue(trade, now=datetime.now(UTC))
    return {"intent": trade.model_dump(mode="json"), "permit": permit.model_dump(mode="json")}


def test_routes_require_the_token_and_report_the_boundary(fake, db) -> None:
    with TestClient(create_app(executor(fake, db))) as client:
        health = client.get("/health").json()
        assert health == {
            "status": "ok",
            "venue_environment": "TESTNET",
            "financial_connectivity": False,
            "real_money_execution": False,
        }
        assert client.get("/v1/ledger").status_code == 401
        wrong = {"Authorization": "Bearer " + "x" * 32}
        assert client.get("/v1/ledger", headers=wrong).status_code == 401
        ledger = client.get("/v1/ledger", headers=AUTH).json()
        assert Decimal(ledger["balances"]["USDT"]["total"]) == 10000
        assert ledger["account_id"] == "binance-spot-testnet-main"


def test_permitted_order_fills_and_permits_stay_single_use(fake, db) -> None:
    with TestClient(create_app(executor(fake, db))) as client:
        trade = intent()
        permit = SIGNER.issue(trade, now=datetime.now(UTC))
        first = client.post("/v1/orders", json=body(trade, permit), headers=AUTH)
        assert first.status_code == 200, first.text
        order = first.json()["order"]
        assert order["status"] == "FILLED"
        assert first.json()["venue_halted"] is False

        # A retried dispatch of the same intent returns the same order, nothing is resent.
        posts = sum(r.method == "POST" for r in fake.requests)
        again = client.post("/v1/orders", json=body(trade, permit), headers=AUTH)
        assert again.json()["order"]["client_order_id"] == order["client_order_id"]
        assert sum(r.method == "POST" for r in fake.requests) == posts

        other = intent()
        reused = client.post("/v1/orders", json=body(other, permit), headers=AUTH)
        assert reused.status_code == 422
        assert reused.json()["detail"] == "permit_intent_mismatch"
        forged = PermitSigner(b"f" * 32).issue(other, now=datetime.now(UTC))
        assert (
            client.post("/v1/orders", json=body(other, forged), headers=AUTH).json()["detail"]
            == "permit_bad_signature"
        )
        assert client.post("/v1/reconcile", headers=AUTH).json()["consistent"] is True


def test_divergence_halts_until_the_venue_agrees_again(fake, db) -> None:
    with TestClient(create_app(executor(fake, db))) as client:
        fake.balances["USDT"] -= Decimal("1")  # money moved outside the system
        result = client.post("/v1/reconcile", headers=AUTH).json()
        assert result["consistent"] is False and result["venue_halted"] is True
        assert client.get("/health").json()["status"] == "halted"
        assert client.post("/v1/orders", json=body(intent()), headers=AUTH).status_code == 409
        assert client.post("/v1/halt/release", headers=AUTH).status_code == 409

        fake.balances["USDT"] += Decimal("1")
        assert client.post("/v1/halt/release", headers=AUTH).json()["venue_halted"] is False
        assert client.post("/v1/orders", json=body(intent()), headers=AUTH).status_code == 200


async def test_restart_recovers_resting_orders_and_polling_applies_fills(fake, db) -> None:
    first = executor(fake, db)
    await first.start()
    resting = intent(limit_price=Decimal("60000.00"), time_in_force=TimeInForce.GTC)
    order = await first.submit(resting, SIGNER.issue(resting, now=datetime.now(UTC)))
    assert order.status.value == "ACKNOWLEDGED"
    await first.stop()

    second = executor(fake, db)  # the process restarts; Binance kept the order
    await second.start()
    assert second.oms is not None
    recovered = second.oms.get(order.client_order_id)
    assert recovered is not None and recovered.status.value == "UNKNOWN"
    resolved = await second.oms.resolve_unknown(order.client_order_id)
    assert resolved.status.value == "ACKNOWLEDGED"

    fake.fill(order.client_order_id)  # the market trades through the resting order
    await second.poll_open_orders()
    assert resolved.status.value == "FILLED"
    assert (await second.reconcile())["consistent"] is True
    usdt = (await second.ledger.balances())["USDT"]
    assert usdt.reserved == 0
    await second.stop()


async def test_live_adapters_and_settings_are_refused(fake, db) -> None:
    live = Executor(
        settings(),
        adapter_factory=factory(fake, VenueEnvironment.LIVE),
        engine=create_async_engine(db),
    )
    with pytest.raises(RuntimeError, match="real-money"):
        await live.start()
    with pytest.raises(ValidationError, match="testnet only"):
        settings(environment="LIVE")
    with pytest.raises(ValidationError, match="at least 32"):
        settings(api_token="short")


# --- public API -> executor (private network) ----------------------------------------


@pytest.fixture
async def api_with_executor(fake, db, monkeypatch):
    from apps.api.app import agent_surface, main, oms_surface, risk_surface, signal_surface
    from apps.api.app.executor_client import ExecutorClient
    from apps.api.app.settings import Settings

    configured = Settings(
        oms_execution_backend="executor",
        executor_url="http://executor.internal:8100",
        executor_api_token=TOKEN,
        risk_permit_signing_key=KEY,
        live_market_source="BINANCE",
    )
    for module in (main, signal_surface, agent_surface, risk_surface, oms_surface):
        monkeypatch.setattr(module, "settings", configured)
    risk_surface.reset_risk_state()
    oms_surface.reset_oms_state()
    monkeypatch.setattr(
        agent_surface.live_monitor,
        "snapshot",
        lambda symbol: {
            "symbol": symbol,
            "venue": "BINANCE",
            "bid": 62_000.00,
            "ask": 62_000.20,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )

    runtime = executor(fake, db)
    await runtime.start()
    app = create_app(runtime)
    app.state.executor = runtime  # ASGITransport does not run the lifespan
    transport = httpx.ASGITransport(app=app)
    client = ExecutorClient(
        configured.executor_url,
        TOKEN,
        client=httpx.AsyncClient(transport=transport),
    )
    monkeypatch.setattr(oms_surface, "executor_client", lambda: client)
    yield oms_surface, risk_surface
    await runtime.stop()
    oms_surface.reset_oms_state()
    risk_surface.reset_risk_state()


async def test_api_dispatches_permitted_intents_to_the_executor(api_with_executor, fake) -> None:
    from apps.api.app.event_state import operational_journal

    oms_surface, risk_surface = api_with_executor
    state = await oms_surface.account_state("USDT")
    assert state.equity == Decimal("10000") and state.gross_exposure == 0

    trade = intent()
    order = await oms_surface.execute(trade, SIGNER.issue(trade, now=datetime.now(UTC)))
    assert order is not None and order["status"] == "FILLED"
    assert operational_journal.journal.recent(1)[0].event_type == "ORDER_FILLED"

    view = await oms_surface.ledger_view()
    assert view["mode"] == "TESTNET"
    assert Decimal(view["balances"]["BTC"]["total"]) == Decimal("0.00999")
    held = await oms_surface.account_state("USDT")
    assert held.positions[BTC_ID] == Decimal("0.00999")
    assert (await oms_surface.reconcile())["consistent"] is True
    assert (await oms_surface.list_orders())["orders"][0]["client_order_id"] == order[
        "client_order_id"
    ]

    fake.balances["USDT"] -= Decimal("5")
    halted = await oms_surface.reconcile()
    assert halted["venue_halted"] is True
    assert "BINANCE_SPOT" in risk_surface.governor().snapshot()["killed_venues"]


async def test_unreachable_executor_fails_closed(api_with_executor, monkeypatch) -> None:
    from apps.api.app.event_state import operational_journal
    from apps.api.app.executor_client import ExecutorClient

    oms_surface, _ = api_with_executor

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to executor", request=request)

    unreachable = ExecutorClient(
        "http://executor.internal:8100",
        TOKEN,
        client=httpx.AsyncClient(transport=httpx.MockTransport(down)),
    )
    monkeypatch.setattr(oms_surface, "executor_client", lambda: unreachable)

    state = await oms_surface.account_state("USDT")
    assert state.gross_exposure == Decimal("Infinity")  # no new risk
    assert state.equity == state.start_of_day_equity  # never read as a loss
    trade = intent()
    assert await oms_surface.execute(trade, SIGNER.issue(trade, now=datetime.now(UTC))) is None
    assert operational_journal.journal.recent(1)[0].event_type == "ORDER_DISPATCH_UNKNOWN"


def test_executor_backend_settings_are_complete() -> None:
    from apps.api.app.settings import Settings

    with pytest.raises(ValidationError, match="EXECUTOR_URL"):
        Settings(oms_execution_backend="executor")
    with pytest.raises(ValidationError, match="EXECUTOR_API_TOKEN"):
        Settings(oms_execution_backend="executor", executor_url="http://x", executor_api_token="s")
    with pytest.raises(ValidationError, match="RISK_PERMIT_SIGNING_KEY"):
        Settings(
            oms_execution_backend="executor", executor_url="http://x", executor_api_token=TOKEN
        )
