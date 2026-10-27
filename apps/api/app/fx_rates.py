"""BRL rate of a quote currency, for the canary cap (R$500) only.

The canary cap is in BRL, but a Binance spot account counts in USDT. The Risk Engine sizes a CANARY
intent on `canary_capital_in(rate)`, where `rate` is the public USDTBRL ask (BRL per USDT). The ask
is the higher side, so it gives the smaller cap. No fresh, sane rate means no canary: the Risk
Engine then refuses the intent (`canary_capital_known`).

Public market data only: no credentials, no venue adapter.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from services.execution.constitution import CANARY_CAPITAL, canary_capital_in

logger = logging.getLogger("proto.fx")

# Binance's public market-data host: same data as api.binance.com, without its regional block.
BOOK_TICKER = "https://data-api.binance.vision/api/v3/ticker/bookTicker"
MAX_AGE = timedelta(seconds=30)
# A rate outside this band is a bad print, not a market: refuse rather than size on it.
SANE_BRL_PER_USDT = (Decimal("2"), Decimal("20"))
_PAIRS = {"USDT": "USDTBRL", "USD": "USDTBRL"}
# USD (Alpaca paper, US equities) is read from the same public USDTBRL ask: USDT stands in for the
# dollar, there is no keyless USDBRL source here. A stablecoin can trade a little under the dollar,
# which would make R$ 10,000 look like more dollars than it is, so the USD rate is raised by this
# margin: the reference capital in USD only ever comes out smaller. No rate: no USD capital (0),
# so nothing new is sized (fail closed).
USD_PROXY_MARGIN = Decimal("1.02")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BrlRates:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._client = client
        self._clock = clock
        self._cache: dict[str, tuple[Decimal, datetime]] = {}
        self._lock = asyncio.Lock()

    async def brl_per(self, quote_asset: str) -> Decimal | None:
        """BRL per unit of `quote_asset` (1 for BRL), or None when unknown or stale."""

        if quote_asset == "BRL":
            return Decimal("1")
        pair = _PAIRS.get(quote_asset)
        if pair is None:
            return None
        async with self._lock:
            cached = self._cache.get(pair)
            if cached and self._clock() - cached[1] <= MAX_AGE:
                rate: Decimal | None = cached[0]
            else:
                rate = await self._fetch(pair)
                if rate is not None:
                    self._cache[pair] = (rate, self._clock())
                else:
                    self._cache.pop(pair, None)
        if rate is not None and quote_asset == "USD":
            return rate * USD_PROXY_MARGIN
        return rate

    async def _fetch(self, pair: str) -> Decimal | None:
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(5.0))
        try:
            response = await client.get(BOOK_TICKER, params={"symbol": pair})
            response.raise_for_status()
            body = response.json()
            bid, ask = Decimal(str(body["bidPrice"])), Decimal(str(body["askPrice"]))
        except (httpx.HTTPError, KeyError, InvalidOperation, ValueError, TypeError):
            logger.warning("BRL rate for %s unavailable", pair)
            return None
        finally:
            if self._client is None:
                await client.aclose()
        low, high = SANE_BRL_PER_USDT
        if not (0 < bid <= ask and low <= ask <= high):
            logger.warning("BRL rate for %s rejected: bid %s ask %s", pair, bid, ask)
            return None
        return ask


_rates = BrlRates()


def rates() -> BrlRates:
    return _rates


async def canary_capital(quote_asset: str, *, market_source: str) -> Decimal | None:
    """CANARY_CAPITAL in `quote_asset`, or None when no fresh BRL rate is known."""

    if quote_asset == "BRL":
        return CANARY_CAPITAL
    if market_source != "BINANCE":
        return None
    rate = await rates().brl_per(quote_asset)
    return canary_capital_in(rate) if rate is not None else None
