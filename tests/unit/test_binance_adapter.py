"""Binance Spot adapter against an in-process fake of the REST API (no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

import httpx
import pytest
from fake_binance import BTC_ID, NOW, SECRET, FakeBinance, creds

from services.execution import OrderStatus, OrderType, TimeInForce, TradeIntent, VenueId
from services.execution.venue import VenueOrder
from services.oms import InMemoryLedger, OrderManager, opening_balances, reconcile_balances
from services.risk import PermitLedger, PermitSigner
from services.venues.binance import (
    BinanceError,
    BinanceSpotAdapter,
    KeyNotTradeOnly,
    VenueOutcomeUnknown,
    load_instruments,
)
from services.venues.credentials import (
    CredentialsUnavailable,
    VenueEnvironment,
    load_credentials,
)

SIGNER = PermitSigner(b"k" * 32)


@pytest.fixture
def fake() -> FakeBinance:
    return FakeBinance()


async def adapter_for(fake: FakeBinance, environment=VenueEnvironment.TESTNET):
    client = httpx.AsyncClient(transport=fake.transport())
    instruments = await load_instruments(["BTCUSDT"], environment=environment, client=client)
    return BinanceSpotAdapter(
        creds(environment), instruments=instruments, client=client, clock=lambda: NOW
    )


def order(**overrides) -> VenueOrder:
    values = {
        "client_order_id": "PXabc123",
        "instrument_id": BTC_ID,
        "side": "BUY",
        "quantity": Decimal("0.01"),
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal("62100.00"),
        "time_in_force": TimeInForce.IOC,
    }
    values.update(overrides)
    return VenueOrder(**values)


# --- adapter -------------------------------------------------------------------


async def test_exchange_info_grids_skip_symbols_that_are_not_trading(fake) -> None:
    async with httpx.AsyncClient(transport=fake.transport()) as client:
        grids = await load_instruments(
            ["BTCUSDT", "ETHUSDT"], environment=VenueEnvironment.TESTNET, client=client
        )
    assert list(grids) == [BTC_ID]
    btc = grids[BTC_ID]
    assert (btc.tick_size, btc.lot_size, btc.min_notional) == (
        Decimal("0.01"),
        Decimal("0.00001"),
        Decimal("5"),
    )
    assert fake.requests[0].url.host == "testnet.binance.vision"


async def test_signed_fill_reports_fee_in_the_charged_asset(fake) -> None:
    adapter = await adapter_for(fake)
    report = await adapter.submit(order())
    assert report.status is OrderStatus.FILLED
    assert report.venue is VenueId.BINANCE_SPOT
    assert report.filled_quantity == Decimal("0.01")
    assert report.average_price == Decimal("62000.20")
    assert (report.fee, report.fee_asset) == (Decimal("0.00001"), "BTC")
    sent = dict(parse_qsl(urlsplit(str(fake.requests[-1].url)).query))
    assert sent["newClientOrderId"] == "PXabc123"
    assert sent["timeInForce"] == "IOC"
    assert sent["quantity"] == "0.01" and sent["price"] == "62100"
    assert adapter.real_money_execution is False


async def test_definite_rejection_and_unknown_outcomes(fake) -> None:
    adapter = await adapter_for(fake)
    fake.submit_error = (400, {"code": -2010, "msg": "Account has insufficient balance."})
    rejected = await adapter.submit(order())
    assert rejected.status is OrderStatus.REJECTED
    assert "insufficient balance" in (rejected.reason or "")

    fake.submit_error = (400, {"code": -1007, "msg": "Timeout waiting for response."})
    with pytest.raises(VenueOutcomeUnknown):
        await adapter.submit(order(client_order_id="PXother"))
    fake.submit_error = (502, {})
    with pytest.raises(VenueOutcomeUnknown):
        await adapter.submit(order(client_order_id="PXother"))


async def test_query_cancel_and_expired_ioc(fake) -> None:
    adapter = await adapter_for(fake)
    assert await adapter.query("PXnope", instrument_id=BTC_ID) is None
    with pytest.raises(ValueError):
        await adapter.query("PXnope")

    resting = await adapter.submit(
        order(limit_price=Decimal("60000"), time_in_force=TimeInForce.GTC)
    )
    assert resting.status is OrderStatus.ACKNOWLEDGED
    canceled = await adapter.cancel("PXabc123", instrument_id=BTC_ID)
    assert canceled.status is OrderStatus.CANCELED

    expired = await adapter.submit(order(client_order_id="PXioc", limit_price=Decimal("60000")))
    assert expired.status is OrderStatus.CANCELED and expired.reason == "EXPIRED"

    fake.partial_then_expire = True
    partial = await adapter.submit(order(client_order_id="PXpartial"))
    assert partial.status is OrderStatus.CANCELED
    assert partial.filled_quantity == Decimal("0.005")


async def test_duplicate_client_id_reports_the_existing_order(fake) -> None:
    adapter = await adapter_for(fake)
    first = await adapter.submit(order())
    again = await adapter.submit(order())
    assert again.status is OrderStatus.FILLED
    assert again.venue_order_id == first.venue_order_id
    assert again.fee == first.fee


async def test_clock_skew_rejection_forces_a_resync(fake) -> None:
    adapter = await adapter_for(fake)
    fake.submit_error = (400, {"code": -1021, "msg": "Timestamp outside of the recvWindow."})
    report = await adapter.submit(order())
    assert report.status is OrderStatus.REJECTED
    assert adapter._offset_ms is None


async def test_live_keys_must_be_trade_only(fake) -> None:
    adapter = await adapter_for(fake, VenueEnvironment.LIVE)
    assert adapter.real_money_execution is True
    assert fake.requests[0].url.host == "api.binance.com"
    fake.restrictions = {
        "ipRestrict": True,
        "enableSpotAndMarginTrading": True,
        "enableWithdrawals": True,
    }
    with pytest.raises(KeyNotTradeOnly, match="enableWithdrawals"):
        await adapter.verify_trade_only()
    fake.restrictions = {"ipRestrict": False, "enableSpotAndMarginTrading": True}
    with pytest.raises(KeyNotTradeOnly, match="ipRestrict"):
        await adapter.verify_trade_only()
    fake.restrictions = {"ipRestrict": True, "enableSpotAndMarginTrading": True}
    assert (await adapter.verify_trade_only())["enableWithdrawals"] is False

    testnet = await adapter_for(fake)
    with pytest.raises(RuntimeError):
        await testnet.verify_trade_only()


# --- through the OMS -------------------------------------------------------------


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
        "created_at": NOW,
    }
    values.update(overrides)
    return TradeIntent.model_validate(values)


async def oms_for(fake: FakeBinance, adapter=None):
    adapter = adapter or await adapter_for(fake)
    ledger = InMemoryLedger(account_id="testnet-main", venue=VenueId.BINANCE_SPOT)
    await ledger.post(
        opening_balances(
            await adapter.balances(),
            account_id="testnet-main",
            venue=VenueId.BINANCE_SPOT,
            now=NOW,
        )
    )
    oms = OrderManager(
        adapter=adapter,
        ledger=ledger,
        permits=PermitLedger(SIGNER),
        instruments=adapter._instruments,
        clock=lambda: NOW,
    )
    return oms, ledger, adapter


@pytest.mark.parametrize("fee_asset", ["BTC", "USDT"])
async def test_oms_fill_keeps_ledger_reconciled_with_binance(fake, fee_asset) -> None:
    fake.fee_asset = fee_asset
    oms, ledger, adapter = await oms_for(fake)
    buy = intent()
    filled = await oms.submit(buy, SIGNER.issue(buy, now=NOW))
    assert filled.status is OrderStatus.FILLED
    balances = await ledger.balances()
    assert balances["USDT"].reserved == 0
    assert reconcile_balances(balances, await adapter.balances()) == []

    sell = intent(
        side="SELL", reduce_only=True, quantity=Decimal("0.009"), limit_price=Decimal("61900")
    )
    sold = await oms.submit(sell, SIGNER.issue(sell, now=NOW))
    assert sold.status is OrderStatus.FILLED
    assert reconcile_balances(await ledger.balances(), await adapter.balances()) == []


async def test_oms_resolves_a_lost_response_by_query_not_resend(fake) -> None:
    oms, ledger, adapter = await oms_for(fake)
    fake.lose_submit_response = True
    trade = intent()
    order_ = await oms.submit(trade, SIGNER.issue(trade, now=NOW))
    assert order_.status is OrderStatus.UNKNOWN
    posts = [r for r in fake.requests if r.method == "POST"]

    fake.lose_submit_response = False
    resolved = await oms.resolve_unknown(order_.client_order_id)
    assert resolved.status is OrderStatus.FILLED
    assert [r for r in fake.requests if r.method == "POST"] == posts  # never resent
    assert reconcile_balances(await ledger.balances(), await adapter.balances()) == []


async def test_oms_refuses_a_live_binance_adapter(fake) -> None:
    live = await adapter_for(fake, VenueEnvironment.LIVE)
    with pytest.raises(ValueError, match="real-money"):
        await oms_for(fake, live)


# --- credentials -------------------------------------------------------------------


def test_credentials_are_scoped_redacted_and_file_backed(tmp_path) -> None:
    prefix = "PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN"
    secret_file = tmp_path / "secret"
    secret_file.write_text("from-vault\n", encoding="utf-8")
    loaded = load_credentials(
        VenueId.BINANCE_SPOT,
        VenueEnvironment.TESTNET,
        "main",
        env={f"{prefix}_API_KEY": "k", f"{prefix}_API_SECRET_FILE": str(secret_file)},
    )
    assert loaded.api_secret == "from-vault"
    assert "from-vault" not in repr(loaded) and "from-vault" not in str(loaded)
    assert "api_key" not in repr(loaded) and "api_secret" not in repr(loaded)

    with pytest.raises(CredentialsUnavailable, match="LIVE_MAIN_API_KEY"):
        load_credentials(
            VenueId.BINANCE_SPOT,
            VenueEnvironment.LIVE,
            "main",
            env={f"{prefix}_API_KEY": "k", f"{prefix}_API_SECRET": "s"},
        )
    with pytest.raises(ValueError):
        load_credentials(VenueId.BINANCE_SPOT, VenueEnvironment.TESTNET, "a b", env={})


def test_binance_error_carries_code() -> None:
    error = BinanceError(400, -2010, "rejected")
    assert error.code == -2010 and "rejected" in str(error)
    assert json.dumps({"code": error.code})


async def test_smoke_tool_is_read_only(fake, monkeypatch) -> None:
    from services.venues import binance_smoke

    prefix = "PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN"
    monkeypatch.setenv(f"{prefix}_API_KEY", "key-123")
    monkeypatch.setenv(f"{prefix}_API_SECRET", SECRET)
    original = httpx.AsyncClient

    def client(*args, **kwargs):
        return original(transport=fake.transport())

    monkeypatch.setattr(httpx, "AsyncClient", client)
    fake.server_time_ms = int(datetime.now(UTC).timestamp() * 1000)
    fake.time_tolerance_ms = 5_000  # the tool runs on the real clock
    report = await binance_smoke.run(VenueEnvironment.TESTNET, "main", ["BTCUSDT"])
    assert report["signed_query_ok"] is True
    assert report["real_money_execution"] is False
    assert abs(report["clock_offset_ms"]) < 5_000
    assert SECRET not in json.dumps(report)
    assert {r.method for r in fake.requests} == {"GET"}
