from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class StopLossDecision:
    triggered: bool
    side: str | None = None
    quantity: float = 0.0
    threshold_price: float | None = None
    reason: str = "NO_OPEN_POSITION"


def evaluate_stop_loss(
    *,
    position_quantity: float,
    average_price: float,
    bid: float,
    ask: float,
    stop_loss_fraction: float,
) -> StopLossDecision:
    """Evaluate a paper-only protective exit using executable-side prices.

    Long positions are tested against the current bid; short positions against
    the current ask. The function is pure and has no financial connectivity.
    """
    values = (position_quantity, average_price, bid, ask, stop_loss_fraction)
    if not all(isfinite(value) for value in values):
        raise ValueError("stop-loss inputs must be finite")
    if average_price <= 0 or bid <= 0 or ask <= 0 or ask < bid:
        raise ValueError("invalid market/position prices")
    if not 0 < stop_loss_fraction <= 0.50:
        raise ValueError("stop_loss_fraction must be in (0, 0.50]")
    if position_quantity == 0:
        return StopLossDecision(triggered=False)

    if position_quantity > 0:
        threshold = average_price * (1.0 - stop_loss_fraction)
        triggered = bid <= threshold
        return StopLossDecision(
            triggered=triggered,
            side="SELL" if triggered else None,
            quantity=abs(position_quantity) if triggered else 0.0,
            threshold_price=threshold,
            reason="LONG_STOP_LOSS" if triggered else "LONG_WITHIN_STOP",
        )

    threshold = average_price * (1.0 + stop_loss_fraction)
    triggered = ask >= threshold
    return StopLossDecision(
        triggered=triggered,
        side="BUY" if triggered else None,
        quantity=abs(position_quantity) if triggered else 0.0,
        threshold_price=threshold,
        reason="SHORT_STOP_LOSS" if triggered else "SHORT_WITHIN_STOP",
    )
