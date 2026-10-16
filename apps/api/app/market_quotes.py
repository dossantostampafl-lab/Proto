"""Live quotes for tradable instruments, shared by the agent pipeline and the Risk Engine."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from services.execution.venue import Quote

from .live_monitor import live_monitor


def live_quote(instrument_id: str, *, market_source: str) -> Quote | None:
    """Live public quote for BINANCE_SPOT:<BASE>USDT while the monitor streams Binance."""

    venue, _, symbol = instrument_id.partition(":")
    if venue != "BINANCE_SPOT" or not symbol.endswith("USDT") or market_source != "BINANCE":
        return None
    snapshot = live_monitor.snapshot(symbol.removesuffix("USDT"))
    # The public feed labels ticks "binance-public-spot": match the family, not one spelling.
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
