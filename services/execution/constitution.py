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
CANARY_CAPITAL: Final = Decimal("500")  # BRL


def canary_capital_in(brl_per_quote: Decimal) -> Decimal:
    """CANARY_CAPITAL in the account's quote currency (BRL per unit of quote: 1 for BRL).

    Pass the highest rate seen (the ask): a higher rate gives a smaller cap, never a larger one.
    """

    if not brl_per_quote > 0:
        raise ValueError("brl_per_quote must be positive")
    return CANARY_CAPITAL / brl_per_quote


def reference_capital_in(brl_per_quote: Decimal) -> Decimal:
    """REFERENCE_CAPITAL in the account's quote currency (BRL per unit of quote: 1 for BRL).

    A USDT account is held to R$ 10,000 worth of USDT, not to 10,000 USDT. Pass the ask, as for
    `canary_capital_in`: the higher rate gives the smaller capital.
    """

    if not brl_per_quote > 0:
        raise ValueError("brl_per_quote must be positive")
    return REFERENCE_CAPITAL / brl_per_quote


def conservative_equity(
    current_equity: Decimal, reference_capital: Decimal = REFERENCE_CAPITAL
) -> Decimal:
    """Size against the lower of reference capital and current equity, never above it.

    `reference_capital` is in the account's quote currency (`reference_capital_in`); it may only
    be tighter than REFERENCE_CAPITAL taken as a plain number, and 0 (unknown) sizes nothing.
    """

    if current_equity <= 0 or reference_capital <= 0:
        return Decimal("0")
    return min(current_equity, reference_capital, REFERENCE_CAPITAL)
