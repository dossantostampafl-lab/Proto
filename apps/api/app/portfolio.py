from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .models import Asset, Fill, Side, SimulationOrder


class DuplicateOrderError(ValueError):
    pass


@dataclass
class Position:
    asset: Asset
    quantity: float = 0.0
    average_price: float = 0.0
    realized_pnl: float = 0.0
    fees: float = 0.0

    def as_dict(self, mark_price: float | None = None) -> dict[str, float | str | None]:
        unrealized_pnl: float | None = None
        market_value: float | None = None
        if mark_price is not None:
            unrealized_pnl = (mark_price - self.average_price) * self.quantity
            market_value = mark_price * self.quantity

        return {
            "asset": self.asset.value,
            "quantity": round(self.quantity, 10),
            "average_price": round(self.average_price, 10),
            "mark_price": round(mark_price, 10) if mark_price is not None else None,
            "market_value": round(market_value, 10) if market_value is not None else None,
            "realized_pnl": round(self.realized_pnl, 10),
            "unrealized_pnl": (
                round(unrealized_pnl, 10) if unrealized_pnl is not None else None
            ),
            "fees": round(self.fees, 10),
        }


class PaperPortfolio:
    def __init__(self, max_journal_entries: int = 1_000) -> None:
        self._positions: dict[Asset, Position] = {}
        self._journal: list[dict[str, object]] = []
        self._processed_order_ids: set[UUID] = set()
        self._max_journal_entries = max_journal_entries

    def reset(self) -> None:
        self._positions.clear()
        self._journal.clear()
        self._processed_order_ids.clear()

    def has_order(self, order_id: UUID) -> bool:
        return order_id in self._processed_order_ids

    def apply_fill(self, order: SimulationOrder, fill: Fill) -> None:
        if order.id != fill.order_id:
            raise ValueError("fill order id does not match simulation order")
        if order.id in self._processed_order_ids:
            raise DuplicateOrderError(f"duplicate simulation order: {order.id}")

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
        self._append_journal(order, fill)
        self._processed_order_ids.add(order.id)

    def _append_journal(self, order: SimulationOrder, fill: Fill) -> None:
        self._journal.append(
            {
                "order_id": str(fill.order_id),
                "market_id": order.market_id,
                "asset": order.asset.value,
                "side": order.side.value,
                "filled_quantity": fill.filled_quantity,
                "fill_price": fill.fill_price,
                "fee": fill.fee,
                "slippage_bps": fill.slippage_bps,
                "filled_at": fill.filled_at.isoformat(),
            }
        )
        if len(self._journal) > self._max_journal_entries:
            overflow = len(self._journal) - self._max_journal_entries
            del self._journal[:overflow]

    def journal(self, limit: int = 100) -> list[dict[str, object]]:
        safe_limit = min(max(limit, 1), self._max_journal_entries)
        return list(reversed(self._journal[-safe_limit:]))

    def snapshot(self, marks: dict[Asset, float] | None = None) -> dict[str, object]:
        marks = marks or {}
        positions = [
            position.as_dict(marks.get(position.asset))
            for position in sorted(self._positions.values(), key=lambda item: item.asset.value)
        ]
        total_realized_pnl = sum(item.realized_pnl for item in self._positions.values())
        total_unrealized_pnl = sum(
            (marks[item.asset] - item.average_price) * item.quantity
            for item in self._positions.values()
            if item.asset in marks
        )
        total_fees = sum(item.fees for item in self._positions.values())
        return {
            "mode": "SIMULATION",
            "positions": positions,
            "total_realized_pnl": round(total_realized_pnl, 10),
            "total_unrealized_pnl": round(total_unrealized_pnl, 10),
            "total_pnl_after_fees": round(
                total_realized_pnl + total_unrealized_pnl - total_fees,
                10,
            ),
            "total_fees": round(total_fees, 10),
        }


__all__ = ["DuplicateOrderError", "PaperPortfolio", "Position"]