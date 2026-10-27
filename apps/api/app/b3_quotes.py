"""B3 quotes for the API, read through the XP executor (the API never talks to the MT5 bridge).

A background task polls ``GET /v1/quotes`` on the XP executor and keeps the latest bid/ask per
symbol. Readers get the quote with the terminal's own timestamp, so the agents and the Risk Engine
refuse it by age once the market is closed or the feed stops: this module never makes a stale
quote look fresh.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter

from services.execution.catalog import B3_INSTRUMENTS
from services.execution.venue import Quote

logger = logging.getLogger("proto.b3_quotes")

Fetch = Callable[[list[str]], Awaitable[dict[str, Any]]]
POLL_SECONDS = 2.0


def b3_symbols() -> list[str]:
    return sorted(i.base_asset for i in B3_INSTRUMENTS.values())


def parse_quote(raw: dict[str, Any]) -> Quote | None:
    """One terminal quote, or None when it is unusable (missing, crossed, naive timestamp)."""

    try:
        bid, ask = Decimal(str(raw["bid"])), Decimal(str(raw["ask"]))
        observed_at = datetime.fromisoformat(str(raw["observed_at"]))
        if not (0 < bid < ask) or observed_at.tzinfo is None:
            return None
        return Quote(bid=bid, ask=ask, observed_at=observed_at)
    except (KeyError, InvalidOperation, ValueError):
        return None


class B3QuoteFeed:
    def __init__(
        self, fetch: Fetch, symbols: list[str], *, interval: float = POLL_SECONDS, name: str = "b3"
    ) -> None:
        self._name = name
        self._fetch = fetch
        self._symbols = symbols
        self._interval = interval
        self._quotes: dict[str, Quote] = {}
        self._task: asyncio.Task | None = None
        self.failures = 0

    def quote(self, symbol: str) -> Quote | None:
        return self._quotes.get(symbol)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def refresh(self) -> None:
        body = await self._fetch(self._symbols)
        quotes = body.get("quotes", {}) if isinstance(body, dict) else {}
        for symbol in self._symbols:
            parsed = parse_quote(quotes.get(symbol) or {})
            if parsed is None:
                self._quotes.pop(symbol, None)  # unusable now: better none than an old price
            else:
                self._quotes[symbol] = parsed

    async def _run(self) -> None:
        while True:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:  # keep polling; quotes age out on their own timestamps
                self.failures += 1
                logger.exception("%s quote refresh failed", self._name)
            await asyncio.sleep(self._interval)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name=f"proto-{self._name}-quotes"
            )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


_feed: B3QuoteFeed | None = None


def install(feed: B3QuoteFeed | None) -> None:
    global _feed
    _feed = feed


def quote_for(symbol: str) -> Quote | None:
    return _feed.quote(symbol) if _feed is not None else None


router = APIRouter(prefix="/v1/b3", tags=["b3"])
_BOUNDARY = {"financial_connectivity": False, "real_money_execution": False}


@router.get("/quotes")
async def quotes_view() -> dict[str, Any]:
    """Read-only view of the B3 quotes the API holds (nothing is fetched on request).

    Each quote carries the terminal's own timestamp and its age, so a reader can tell a live price
    from the last one before the session closed. A symbol with no usable quote is null.
    """

    from . import settings as settings_module

    if not settings_module.settings.xp_routing_enabled:
        return {"enabled": False, "reason": "XP routing is not configured", **_BOUNDARY}
    now = datetime.now(UTC)
    symbols: dict[str, Any] = {}
    for symbol in b3_symbols():
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
        "source": "XP executor (MT5 terminal, demo account)",
        "feed_running": _feed is not None and _feed.running,
        "refresh_failures": _feed.failures if _feed is not None else 0,
        "symbols": symbols,
        **_BOUNDARY,
    }


async def start_if_enabled() -> B3QuoteFeed | None:
    """Start the feed when XP routing is on. Imported lazily: the OMS imports market_quotes."""

    from .oms_surface import executor_client
    from .settings import settings

    if not settings.xp_routing_enabled:
        return None
    from services.execution import VenueId

    client = executor_client(VenueId.XP_B3)
    feed = B3QuoteFeed(client.quotes, b3_symbols())
    install(feed)
    feed.start()
    return feed


async def stop(feed: B3QuoteFeed | None) -> None:
    install(None)
    if feed is not None:
        await feed.stop()
