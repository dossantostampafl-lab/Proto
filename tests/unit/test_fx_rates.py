"""BRL rate for the canary cap: public USDTBRL ask, cached briefly, refused when not sane."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from apps.api.app import fx_rates
from apps.api.app.fx_rates import BrlRates
from services.execution.constitution import CANARY_CAPITAL


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def rates(answer, clock=None, calls=None) -> BrlRates:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        assert request.url.params["symbol"] == "USDTBRL"
        assert "X-MBX-APIKEY" not in request.headers  # public data, no credential
        return answer(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return BrlRates(client=client, clock=clock or Clock())


def ticker(bid: str, ask: str):
    return lambda request: httpx.Response(200, json={"bidPrice": bid, "askPrice": ask})


async def test_usdt_uses_the_ask_and_caches_it_briefly() -> None:
    clock, calls = Clock(), []
    source = rates(ticker("5.49", "5.50"), clock, calls)
    assert await source.brl_per("USDT") == Decimal("5.50")
    assert await source.brl_per("USDT") == Decimal("5.50")
    assert len(calls) == 1
    clock.now += timedelta(seconds=31)
    await source.brl_per("USDT")
    assert len(calls) == 2
    assert await source.brl_per("BRL") == Decimal("1")
    assert await source.brl_per("EUR") is None


async def test_bad_prints_and_outages_mean_no_rate() -> None:
    assert await rates(ticker("0", "5.5")).brl_per("USDT") is None
    assert await rates(ticker("5.6", "5.5")).brl_per("USDT") is None  # crossed book
    assert await rates(ticker("0.5", "0.6")).brl_per("USDT") is None  # outside the sane band
    assert await rates(lambda r: httpx.Response(503)).brl_per("USDT") is None
    assert await rates(lambda r: httpx.Response(200, json={})).brl_per("USDT") is None

    def down(request):
        raise httpx.ConnectError("offline", request=request)

    assert await rates(down).brl_per("USDT") is None


async def test_canary_capital_is_the_brl_cap_in_the_quote_currency(monkeypatch) -> None:
    monkeypatch.setattr(fx_rates, "_rates", rates(ticker("5.49", "5.50")))
    usdt = await fx_rates.canary_capital("USDT", market_source="BINANCE")
    assert usdt is not None and abs(usdt - Decimal("90.909")) < Decimal("0.001")
    assert await fx_rates.canary_capital("BRL", market_source="SYNTHETIC") == CANARY_CAPITAL
    # Without the live Binance feed there is no rate to trust.
    assert await fx_rates.canary_capital("USDT", market_source="SYNTHETIC") is None
