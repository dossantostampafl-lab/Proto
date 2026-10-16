"""Live quotes for tradable instruments, shared by the agent pipeline and the Risk Engine."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from services.execution.venue import Quote

from . import b3_quotes
from .live_monitor import live_monitor


def live_quote(instrument_id: str, *, market_source: str) -> Quote | None:
    """Live public quote for BINANCE_SPOT:<BASE>USDT while the monitor streams Binance."""

    venue, _, symbol = instrument_id.partition(":")
    if venue == "XP_B3":
        # Read through the XP executor with the terminal's timestamp, so age checks stay honest.
        return b3_quotes.quote_for(symbol)
    if venue != "BINANCE_SPOT" or not symbol.endswith("USDT") or market_source != "BINANCE":
        return None
    snapshot = live_monitor.snapshot(symbol.removesuffix("USDT"))
    # The feed labels its frames "binance-public-spot"; any other feed (Coinbase) is refused.
    if snapshot is None or not str(snapshot.get("venue", "")).upper().startswith("BINANCE"):
        return None
    try:
        return Quote(
            bid=Decimal(str(snapshot["bid"])),
            ask=Decimal(str(snapshot["ask"])),
            observed_at=datetime.fromisoformat(str(snapshot["timestamp"])),
        )
    except (KeyError, InvalidOperation, ValueError):
        return None
