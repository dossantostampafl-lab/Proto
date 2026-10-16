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
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

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
    def __init__(self, fetch: Fetch, symbols: list[str], *, interval: float = POLL_SECONDS) -> None:
        self._fetch = fetch
        self._symbols = symbols
        self._interval = interval
        self._quotes: dict[str, Quote] = {}
        self._task: asyncio.Task | None = None
        self.failures = 0

    def quote(self, symbol: str) -> Quote | None:
        return self._quotes.get(symbol)

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
                logger.exception("B3 quote refresh failed")
            await asyncio.sleep(self._interval)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name="proto-b3-quotes")

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
