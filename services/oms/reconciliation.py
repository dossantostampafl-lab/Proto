"""Ledger vs venue balance reconciliation.

The ledger's `total` per asset must match what the venue reports. Any asset
off by more than its tolerance is a divergence; the caller halts the venue
(kill switch) until an operator investigates.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from .ledger import AssetBalance

DEFAULT_TOLERANCE = Decimal("0.00000001")


@dataclass(frozen=True)
class Divergence:
    asset: str
    ledger: Decimal
    venue: Decimal

    @property
    def difference(self) -> Decimal:
        return self.venue - self.ledger

    def to_dict(self) -> dict[str, str]:
        return {
            "asset": self.asset,
            "ledger": str(self.ledger),
            "venue": str(self.venue),
            "difference": str(self.difference),
        }


def reconcile_balances(
    ledger: Mapping[str, AssetBalance],
    venue: Mapping[str, Decimal],
    *,
    tolerances: Mapping[str, Decimal] | None = None,
) -> list[Divergence]:
    tolerances = tolerances or {}
    divergences = []
    for asset in sorted(set(ledger) | set(venue)):
        ours = ledger[asset].total if asset in ledger else Decimal("0")
        theirs = venue.get(asset, Decimal("0"))
        if abs(theirs - ours) > tolerances.get(asset, DEFAULT_TOLERANCE):
            divergences.append(Divergence(asset, ours, theirs))
    return divergences
