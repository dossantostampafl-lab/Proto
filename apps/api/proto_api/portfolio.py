from __future__ import annotations

from dataclasses import dataclass

from .models import Asset, Fill, Side, SimulationOrder


@dataclass
class Position:
    asset: Asset
    quantity: float = 0.0
    average_price: float = 0.0
    realized_pnl: float = 0.0
    fees: float = 0.0

    def as_dict(self) -> dict[str, float | str]:
        return {
            "asset": self.asset.value,
            "quantity": round(self.quantity, 10),
            "average_price": round(self.average_price, 10),
            "realized_pnl": round(self.realized_pnl, 10),
            "fees": round(self.fees, 10),
        }


class PaperPortfolio:
    def __init__(self) -> None:
        self._positions: dict[Asset, Position] = {}

    def reset(self) -> None:
        self._positions.clear()

    def apply_fill(self, order: SimulationOrder, fill: Fill) -> None:
        position = self._positions.setdefault(order.asset, Position(asset=order.asset))
        position.fees += fill.fee

        signed_fill = fill.filled_quantity if order.side == Side.BUY else -fill.filled_quantity
        old_quantity = position.quantity
        new_quantity = old_quantity + signed_fill

        same_direction = old_quantity == 0 or old_quantity * signed_fill > 0
        if same_direction:
            old_notional = abs(old_quantity) * position.average_price
            added_notional = abs(signed_fill) * fill.fill_price
            total_quantity = abs(old_quantity) + abs(signed_fill)
            position.average_price = (old_notional + added_notional) / total_quantity
        else:
            closing_quantity = min(abs(old_quantity), abs(signed_fill))
            direction = 1.0 if old_quantity > 0 else -1.0
            position.realized_pnl += (
                (fill.fill_price - position.average_price) * closing_quantity * direction
            )
            if abs(signed_fill) > abs(old_quantity):
                position.average_price = fill.fill_price
            elif new_quantity == 0:
                position.average_price = 0.0

        position.quantity = new_quantity

    def snapshot(self) -> dict[str, object]:
        positions = [
            position.as_dict()
            for position in sorted(self._positions.values(), key=lambda item: item.asset.value)
        ]
        total_realized_pnl = sum(item.realized_pnl for item in self._positions.values())
        total_fees = sum(item.fees for item in self._positions.values())
        return {
            "mode": "SIMULATION",
            "positions": positions,
            "total_realized_pnl": round(total_realized_pnl, 10),
            "total_fees": round(total_fees, 10),
        }
