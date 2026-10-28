"""US equity quotes for the API, read through the Alpaca executor (the API never holds Alpaca keys).

Same pattern as ``b3_quotes``: a background task polls ``GET /v1/quotes`` on the Alpaca executor
and keeps the latest bid/ask per symbol with Alpaca's own timestamp, so the agents and the Risk
Engine refuse a quote by age once the market closes or the feed stops.

The feed is Alpaca's free **IEX** feed: quotes from one exchange (IEX), not the consolidated SIP
tape. Its spread can be wider than the national best bid/offer, and outside IEX hours there is no
quote at all. Every view says so.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from services.execution.catalog import US_INSTRUMENTS
from services.execution.venue import Quote

from .b3_quotes import B3QuoteFeed, Fetch
from .executor_client import ExecutorRefused

logger = logging.getLogger("proto.us_quotes")

FEED_LABEL = "IEX"
SOURCE = "Alpaca executor (IEX feed, single exchange, not consolidated SIP; paper account)"


def us_symbols() -> list[str]:
    return sorted(i.base_asset for i in US_INSTRUMENTS.values())


_feed: B3QuoteFeed | None = None
# Symbols of the catalog the Alpaca executor was not configured for (its ALPACA_SYMBOLS): the
# executor refuses a whole request that names one, so they are left out of the next requests.
_unconfigured: set[str] = set()
_NOT_CONFIGURED = re.compile(r"not configured on this executor: ([A-Z0-9., -]+)")


def skipping_unconfigured(fetch: Fetch) -> Fetch:
    """``fetch`` without the symbols the executor says it was not configured for.

    One refused name used to fail every read, the configured ones included (2026-10-28: AAPL in
    the catalog, SPY,QQQ on the executor, no US quote at all). The refusal names the symbols; they
    are dropped and the read is retried once, so the others keep flowing and the dropped ones show
    no quote, never an invented one.
    """

    async def read(names: list[str]) -> dict[str, Any]:
        wanted = [n for n in names if n not in _unconfigured]
        if not wanted:
            return {"quotes": {}}
        try:
            return await fetch(wanted)
        except ExecutorRefused as exc:
            found = _NOT_CONFIGURED.search(exc.detail) if exc.status == 422 else None
            if found is None:
                raise
            dropped = {n.strip() for n in found.group(1).split(",") if n.strip()}
            if not dropped & set(wanted):
                raise
            _unconfigured.update(dropped)
            logger.warning("US quotes: Alpaca executor not configured for %s", sorted(dropped))
            rest = [n for n in wanted if n not in _unconfigured]
            return await fetch(rest) if rest else {"quotes": {}}

    return read


def install(feed: B3QuoteFeed | None) -> None:
    global _feed
    _feed = feed
    _unconfigured.clear()


def quote_for(symbol: str) -> Quote | None:
    return _feed.quote(symbol) if _feed is not None else None


router = APIRouter(prefix="/v1/us", tags=["us-equities"])
_BOUNDARY = {"financial_connectivity": False, "real_money_execution": False}


@router.get("/quotes")
async def quotes_view() -> dict[str, Any]:
    """Read-only view of the US quotes the API holds (nothing is fetched on request)."""

    from . import settings as settings_module

    if not settings_module.settings.alpaca_routing_enabled:
        return {"enabled": False, "reason": "Alpaca routing is not configured", **_BOUNDARY}
    now = datetime.now(UTC)
    symbols: dict[str, Any] = {}
    for symbol in us_symbols():
        quote = quote_for(symbol)
        symbols[symbol] = (
            None
            if quote is None
            else {
                "bid": str(quote.bid),
                "ask": str(quote.ask),
                "observed_at": quote.observed_at.isoformat(),
                "age_seconds": max(0.0, (now - quote.observed_at).total_seconds()),
            }
        )
    return {
        "enabled": True,
        "source": SOURCE,
        "feed": FEED_LABEL,
        "consolidated": False,
        "data_mode": "PAPER",
        "feed_running": _feed is not None and _feed.running,
        "refresh_failures": _feed.failures if _feed is not None else 0,
        "not_configured_on_executor": sorted(_unconfigured),
        "symbols": symbols,
        **_BOUNDARY,
    }


async def start_if_enabled() -> B3QuoteFeed | None:
    """Start the feed when Alpaca routing is on. Imported lazily: the OMS imports market_quotes."""

    from .oms_surface import executor_client
    from .settings import settings

    if not settings.alpaca_routing_enabled:
        return None
    from services.execution import VenueId

    client = executor_client(VenueId.ALPACA_US)
    # IEX quotes move slower than a terminal's: polling every 5 s stays well inside the free
    # plan's request budget and inside the Risk Engine's 10 s quote age.
    feed = B3QuoteFeed(skipping_unconfigured(client.quotes), us_symbols(), interval=5.0, name="us")
    install(feed)
    feed.start()
    return feed


async def stop(feed: B3QuoteFeed | None) -> None:
    install(None)
    if feed is not None:
        await feed.stop()
