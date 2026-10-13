"""Conservative BRL account state sourced exclusively from the Phase 4 ledger.

Valuations and risk watermarks are durable. A missing/stale mark, unknown
asset, or missing baseline is a refusal, never reference-capital fallback.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from services.risk.engine import AccountState

from .oms import (
    OrderRejected,
    equity_checkpoints,
    opening_balances,
    reservations,
)
from .venue import Quote

if TYPE_CHECKING:
    from .oms import CanonicalPaperOMS


async def risk_account(
    oms: CanonicalPaperOMS,
    *,
    now: datetime,
    quotes: Callable[[str], Quote | None],
    max_quote_age: timedelta = timedelta(seconds=10),
) -> AccountState:
    """Valuate positions at the bid and pending BUY commitments at reserved cost."""

    if max_quote_age <= timedelta(0):
        raise ValueError("max_quote_age must be positive")
    async with oms.engine.begin() as connection:
        venue = oms.adapter.venue.value
        initialized = await connection.scalar(
            select(opening_balances.c.asset)
            .where(opening_balances.c.venue == venue)
            .limit(1)
        )
        if initialized is None:
            raise OrderRejected("ledger_uninitialized")
        checkpoint = (
            await connection.execute(
                select(equity_checkpoints)
                .where(equity_checkpoints.c.venue == venue)
                .with_for_update()
            )
        ).mappings().first()
        if checkpoint is None:
            raise OrderRejected("equity_baseline_unavailable")

        balances = await oms._balances_locked(connection)
        brl_cash = balances.get("BRL", Decimal("0"))
        positions: dict[str, Decimal] = {}
        valued_assets: set[str] = set()
        gross_exposure = Decimal("0")
        equity = brl_cash

        for instrument in oms._instruments.values():
            if instrument.venue.value != venue or instrument.quote_asset != "BRL":
                continue
            quantity = balances.get(instrument.base_asset, Decimal("0"))
            if quantity < 0:
                raise OrderRejected("negative_spot_position")
            if quantity == 0:
                continue
            if instrument.base_asset in valued_assets:
                raise OrderRejected("ambiguous_asset_mark")
            quote = quotes(instrument.instrument_id)
            if (
                quote is None
                or not (Decimal("0") < quote.bid < quote.ask)
                or not timedelta(0) <= now - quote.observed_at <= max_quote_age
            ):
                raise OrderRejected("market_data_unavailable")
            valued_assets.add(instrument.base_asset)
            positions[instrument.instrument_id] = quantity
            equity += quantity * quote.bid
            gross_exposure += quantity * quote.ask

        if any(
            amount != 0 and asset != "BRL" and asset not in valued_assets
            for asset, amount in balances.items()
        ):
            raise OrderRejected("unvalued_asset_or_currency")

        pending = (
            await connection.execute(
                select(reservations)
                .where(reservations.c.venue == venue, reservations.c.asset == "BRL")
            )
        ).mappings().all()
        gross_exposure += sum(
            (Decimal(row["remaining"]) for row in pending), Decimal("0")
        )

        today = now.date().isoformat()
        day_start = (
            Decimal(checkpoint["start_of_day_equity"])
            if checkpoint["day"] == today
            else equity
        )
        peak = max(Decimal(checkpoint["peak_equity"]), equity)
        if (
            checkpoint["day"] != today
            or peak != Decimal(checkpoint["peak_equity"])
        ):
            await connection.execute(
                update(equity_checkpoints)
                .where(equity_checkpoints.c.venue == venue)
                .values(
                    day=today,
                    start_of_day_equity=str(day_start),
                    peak_equity=str(peak),
                )
            )

    return AccountState(
        equity=equity,
        start_of_day_equity=day_start,
        peak_equity=peak,
        gross_exposure=gross_exposure,
        positions=positions,
    )
