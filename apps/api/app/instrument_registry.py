"""The instruments the API may size, approve and route.

Binance paper/testnet instruments always. The B3 fractional symbols join only when XP routing is
configured, so an API without ``XP_EXECUTOR_URL`` knows nothing about B3 and refuses its intents
exactly as before.
"""

from __future__ import annotations

from services.execution import TradableInstrument
from services.execution.catalog import B3_INSTRUMENTS, PAPER_INSTRUMENTS

from .settings import settings


def active_instruments() -> dict[str, TradableInstrument]:
    if settings.xp_routing_enabled:
        return {**PAPER_INSTRUMENTS, **B3_INSTRUMENTS}
    return PAPER_INSTRUMENTS
