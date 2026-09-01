from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

from .models import Asset, Fill, Side, SimulationOrder
from .portfolio import PaperPortfolio


def _restore_timestamp(value: object) -> datetime:
    restored = datetime.fromisoformat(str(value))
    if restored.tzinfo is None or restored.utcoffset() is None:
        return restored.replace(tzinfo=UTC)
    return restored.astimezone(UTC)


async def recover_paper_portfolio(
    portfolio: PaperPortfolio,
    entries: AsyncIterator[dict[str, object]],
) -> int:
    """Rebuild deterministic paper state from durable fills in chronological order."""

    portfolio.reset()
    recovered = 0
    async for entry in entries:
        order_id = UUID(str(entry["order_id"]))
        market_id = str(entry["market_id"])
        asset = Asset(str(entry["asset"]))
        side = Side(str(entry["side"]))
        quantity = float(entry["filled_quantity"])
        fill_price = float(entry["fill_price"])
        filled_at = _restore_timestamp(entry["filled_at"])
        fill = Fill(
            order_id=order_id,
            market_id=market_id,
            asset=asset,
            side=side,
            filled_quantity=quantity,
            fill_price=fill_price,
            fee=float(entry["fee"]),
            slippage_bps=float(entry["slippage_bps"]),
            filled_at=filled_at,
        )
        order = SimulationOrder(
            id=order_id,
            market_id=market_id,
            asset=asset,
            side=side,
            quantity=quantity,
            limit_price=fill_price,
            created_at=filled_at,
        )
        portfolio.apply_fill(order, fill)
        recovered += 1
    return recovered


__all__ = ["recover_paper_portfolio"]
