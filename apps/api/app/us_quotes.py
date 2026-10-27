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
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from services.execution.catalog import US_INSTRUMENTS
from services.execution.venue import Quote

from .b3_quotes import B3QuoteFeed

logger = logging.getLogger("proto.us_quotes")

FEED_LABEL = "IEX"
SOURCE = "Alpaca executor (IEX feed, single exchange, not consolidated SIP; paper account)"


def us_symbols() -> list[str]:
    return sorted(i.base_asset for i in US_INSTRUMENTS.values())


_feed: B3QuoteFeed | None = None


def install(feed: B3QuoteFeed | None) -> None:
    global _feed
    _feed = feed


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
    feed = B3QuoteFeed(client.quotes, us_symbols(), interval=5.0, name="us")
    install(feed)
    feed.start()
    return feed


async def stop(feed: B3QuoteFeed | None) -> None:
    install(None)
    if feed is not None:
        await feed.stop()
