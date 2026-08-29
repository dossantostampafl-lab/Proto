from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal


ShadowAction = Literal["OBSERVE", "WOULD_BUY", "WOULD_SELL"]


@dataclass(frozen=True)
class ShadowDecision:
    symbol: str
    action: ShadowAction
    model_probability: float
    market_probability: float
    net_edge: float
    confidence: float
    observed_at: datetime
    creates_fill: bool = False
    submits_external_order: bool = False


class ShadowDecisionEngine:
    """Evaluate model-vs-market state without creating orders or fills."""

    def __init__(self, *, minimum_abs_edge: float = 0.01, minimum_confidence: float = 0.60) -> None:
        if minimum_abs_edge < 0:
            raise ValueError("minimum_abs_edge must be non-negative")
        if not 0 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be between 0 and 1")
        self.minimum_abs_edge = minimum_abs_edge
        self.minimum_confidence = minimum_confidence

    def evaluate(
        self,
        *,
        symbol: str,
        model_probability: float,
        market_probability: float,
        net_edge: float,
        confidence: float,
        observed_at: datetime | None = None,
    ) -> ShadowDecision:
        if not 0 <= model_probability <= 1:
            raise ValueError("model_probability must be between 0 and 1")
        if not 0 <= market_probability <= 1:
            raise ValueError("market_probability must be between 0 and 1")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

        action: ShadowAction = "OBSERVE"
        if confidence >= self.minimum_confidence and abs(net_edge) >= self.minimum_abs_edge:
            action = "WOULD_BUY" if net_edge > 0 else "WOULD_SELL"

        return ShadowDecision(
            symbol=symbol,
            action=action,
            model_probability=model_probability,
            market_probability=market_probability,
            net_edge=net_edge,
            confidence=confidence,
            observed_at=observed_at or datetime.now(UTC),
        )
