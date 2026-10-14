"""Constitutional risk limits (AGENTS.md). Automation may only tighten these."""

from __future__ import annotations

from decimal import Decimal
from typing import Final

REFERENCE_CAPITAL: Final = Decimal("10000")  # BRL
MAX_RISK_PER_POSITION: Final = Decimal("0.0025")  # of conservative current equity
MAX_DAILY_LOSS: Final = Decimal("0.01")
MAX_DRAWDOWN: Final = Decimal("0.05")
MAX_GROSS_LEVERAGE: Final = Decimal("1.0")


# Owner decision (Phase 6): the real-money canary never sizes on more than this.
CANARY_CAPITAL: Final = Decimal("500")


def conservative_equity(current_equity: Decimal) -> Decimal:
    """Size against the lower of reference capital and current equity, never above it."""

    if current_equity <= 0:
        return Decimal("0")
    return min(current_equity, REFERENCE_CAPITAL)
