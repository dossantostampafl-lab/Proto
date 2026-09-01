from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .models import Asset, Fill, Side, SimulationOrder

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


def _decimal(value: int | float | str | Decimal) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _json_number(value: Decimal) -> float:
    return float(round(value, 10))


def _require_aware_timestamp(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass
class Position:
    asset: Asset
    quantity: Decimal = ZERO
    average_price: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    fees: Decimal = ZERO
    opened_at: datetime | None = None
    last_fill_at: datetime | None = None
    temporal_exposure_notional_seconds: Decimal = ZERO

    def accrue_temporal_exposure(self, observed_at: datetime) -> None:
        _require_aware_timestamp(observed_at, field_name="fill timestamp")
        if self.last_fill_at is None:
            self.last_fill_at = observed_at
            return
        if observed_at < self.last_fill_at:
            raise ValueError("fill timestamp cannot move position clock backwards")

        elapsed_seconds = _decimal((observed_at - self.last_fill_at).total_seconds())
        if self.quantity != ZERO and elapsed_seconds > ZERO:
            reference_notional = abs(self.quantity) * self.average_price
            self.temporal_exposure_notional_seconds += reference_notional * elapsed_seconds
        self.last_fill_at = observed_at

    def temporal_exposure_at(
        self,
        *,
        as_of: datetime | None,
        mark_price: Decimal | None,
    ) -> Decimal:
        exposure = self.temporal_exposure_notional_seconds
        if as_of is None or self.last_fill_at is None or self.quantity == ZERO:
            return exposure
        _require_aware_timestamp(as_of, field_name="portfolio as_of timestamp")
        if as_of < self.last_fill_at:
            raise ValueError("portfolio as_of timestamp cannot predate the latest fill")
        elapsed_seconds = _decimal((as_of - self.last_fill_at).total_seconds())
        reference_price = mark_price if mark_price is not None else self.average_price
        return exposure + abs(self.quantity) * reference_price * elapsed_seconds

    def position_age_seconds(self, *, as_of: datetime | None) -> Decimal:
        if self.quantity == ZERO or self.opened_at is None or as_of is None:
            return ZERO
        _require_aware_timestamp(as_of, field_name="portfolio as_of timestamp")
        if as_of < self.opened_at:
            raise ValueError("portfolio as_of timestamp cannot predate position open time")
        return _decimal((as_of - self.opened_at).total_seconds())

    def as_dict(
        self,
        mark_price: float | None = None,
        *,
        as_of: datetime | None = None,
    ) -> dict[str, float | str | None]:
        unrealized_pnl: Decimal | None = None
        market_value: Decimal | None = None
        mark = _decimal(mark_price) if mark_price is not None else None
        if mark is not None:
            unrealized_pnl = (mark - self.average_price) * self.quantity
            market_value = mark * self.quantity

        temporal_exposure = self.temporal_exposure_at(as_of=as_of, mark_price=mark)
        return {
            "asset": self.asset.value,
            "quantity": _json_number(self.quantity),
            "average_price": _json_number(self.average_price),
            "mark_price": _json_number(mark) if mark is not None else None,
            "market_value": _json_number(market_value) if market_value is not None else None,
            "realized_pnl": _json_number(self.realized_pnl),
            "unrealized_pnl": (
                _json_number(unrealized_pnl) if unrealized_pnl is not None else None
            ),
            "fees": _json_number(self.fees),
            "opened_at": self.opened_at.isoformat() if self.opened_at is not None else None,
            "last_fill_at": (
                self.last_fill_at.isoformat() if self.last_fill_at is not None else None
            ),
            "position_age_seconds": _json_number(self.position_age_seconds(as_of=as_of)),
            "temporal_exposure_notional_seconds": _json_number(temporal_exposure),
        }


class PaperPortfolio:
    def __init__(
        self,
        max_journal_entries: int = 1_000,
        *,
        reference_time_provider: Callable[[], datetime | None] | None = None,
    ) -> None:
        self._positions: dict[Asset, Position] = {}
        self._journal: list[dict[str, object]] = []
        self._seen_order_ids: set[str] = set()
        self._max_journal_entries = max_journal_entries
        self._turnover_notional = ZERO
        self._slippage_cost = ZERO
        self._realized_pnl_high_watermark = ZERO
        self._reference_time_provider = reference_time_provider

    def reset(self) -> None:
        self._positions.clear()
        self._journal.clear()
        self._seen_order_ids.clear()
        self._turnover_notional = ZERO
        self._slippage_cost = ZERO
        self._realized_pnl_high_watermark = ZERO

    def has_order(self, order_id: object) -> bool:
        return str(order_id) in self._seen_order_ids

    def _realized_pnl_after_fees(self) -> Decimal:
        return sum(
            (position.realized_pnl - position.fees for position in self._positions.values()),
            start=ZERO,
        )

    def _update_realized_high_watermark(self) -> None:
        self._realized_pnl_high_watermark = max(
            self._realized_pnl_high_watermark,
            self._realized_pnl_after_fees(),
        )

    def apply_fill(self, order: SimulationOrder, fill: Fill) -> bool:
        order_id = str(fill.order_id)
        if order_id in self._seen_order_ids:
            return False

        filled_quantity = _decimal(fill.filled_quantity)
        fill_price = _decimal(fill.fill_price)
        fee = _decimal(fill.fee)
        slippage_bps = _decimal(fill.slippage_bps)

        fill_notional = filled_quantity * fill_price
        self._turnover_notional += fill_notional
        self._slippage_cost += fill_notional * slippage_bps / TEN_THOUSAND

        position = self._positions.setdefault(order.asset, Position(asset=order.asset))
        position.accrue_temporal_exposure(fill.filled_at)
        position.fees += fee

        signed_fill = filled_quantity if order.side == Side.BUY else -filled_quantity
        old_quantity = position.quantity
        new_quantity = old_quantity + signed_fill

        same_direction = old_quantity == ZERO or old_quantity * signed_fill > ZERO
        if same_direction:
            old_notional = abs(old_quantity) * position.average_price
            added_notional = abs(signed_fill) * fill_price
            total_quantity = abs(old_quantity) + abs(signed_fill)
            position.average_price = (old_notional + added_notional) / total_quantity
        else:
            closing_quantity = min(abs(old_quantity), abs(signed_fill))
            direction = Decimal("1") if old_quantity > ZERO else Decimal("-1")
            position.realized_pnl += (
                (fill_price - position.average_price) * closing_quantity * direction
            )
            if abs(signed_fill) > abs(old_quantity):
                position.average_price = fill_price
            elif new_quantity == ZERO:
                position.average_price = ZERO

        if old_quantity == ZERO and new_quantity != ZERO:
            position.opened_at = fill.filled_at
        elif old_quantity != ZERO and new_quantity == ZERO:
            position.opened_at = None
        elif old_quantity * new_quantity < ZERO:
            position.opened_at = fill.filled_at

        position.quantity = new_quantity
        self._seen_order_ids.add(order_id)
        self._append_journal(order, fill)
        self._update_realized_high_watermark()
        return True

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

    def snapshot(
        self,
        marks: dict[Asset, float] | None = None,
        *,
        as_of: datetime | None = None,
    ) -> dict[str, object]:
        marks = marks or {}
        resolved_as_of = as_of
        if resolved_as_of is None and self._reference_time_provider is not None:
            resolved_as_of = self._reference_time_provider()
        if resolved_as_of is not None:
            _require_aware_timestamp(
                resolved_as_of,
                field_name="portfolio as_of timestamp",
            )
        decimal_marks = {asset: _decimal(price) for asset, price in marks.items()}
        ordered_positions = sorted(self._positions.values(), key=lambda item: item.asset.value)
        positions = [
            position.as_dict(marks.get(position.asset), as_of=resolved_as_of)
            for position in ordered_positions
        ]
        total_realized_pnl = sum(
            (item.realized_pnl for item in self._positions.values()),
            start=ZERO,
        )
        total_unrealized_pnl = sum(
            (
                (decimal_marks[item.asset] - item.average_price) * item.quantity
                for item in self._positions.values()
                if item.asset in decimal_marks
            ),
            start=ZERO,
        )
        total_fees = sum((item.fees for item in self._positions.values()), start=ZERO)
        total_execution_cost = total_fees + self._slippage_cost
        realized_pnl_after_fees = total_realized_pnl - total_fees
        realized_drawdown = max(
            self._realized_pnl_high_watermark - realized_pnl_after_fees,
            ZERO,
        )

        exposure_by_asset: dict[str, Decimal] = {}
        net_exposure = ZERO
        for position in ordered_positions:
            if position.quantity == ZERO:
                continue
            price = decimal_marks.get(position.asset, position.average_price)
            signed_exposure = position.quantity * price
            exposure_by_asset[position.asset.value] = abs(signed_exposure)
            net_exposure += signed_exposure

        gross_exposure = sum(exposure_by_asset.values(), start=ZERO)
        max_asset_concentration = (
            max(exposure_by_asset.values()) / gross_exposure
            if gross_exposure > ZERO
            else ZERO
        )
        temporal_exposure = sum(
            (
                position.temporal_exposure_at(
                    as_of=resolved_as_of,
                    mark_price=decimal_marks.get(position.asset),
                )
                for position in ordered_positions
            ),
            start=ZERO,
        )
        max_position_age = max(
            (
                position.position_age_seconds(as_of=resolved_as_of)
                for position in ordered_positions
            ),
            default=ZERO,
        )

        return {
            "mode": "SIMULATION",
            "as_of": resolved_as_of.isoformat() if resolved_as_of is not None else None,
            "positions": positions,
            "open_position_count": len(exposure_by_asset),
            "gross_exposure": _json_number(gross_exposure),
            "net_exposure": _json_number(net_exposure),
            "max_asset_concentration": _json_number(max_asset_concentration),
            "exposure_by_asset": {
                asset: _json_number(value)
                for asset, value in sorted(exposure_by_asset.items())
            },
            "temporal_exposure_notional_seconds": _json_number(temporal_exposure),
            "max_position_age_seconds": _json_number(max_position_age),
            "total_realized_pnl": _json_number(total_realized_pnl),
            "total_unrealized_pnl": _json_number(total_unrealized_pnl),
            "total_pnl_after_fees": _json_number(
                total_realized_pnl + total_unrealized_pnl - total_fees
            ),
            "total_fees": _json_number(total_fees),
            "realized_pnl_high_watermark": _json_number(self._realized_pnl_high_watermark),
            "realized_drawdown": _json_number(realized_drawdown),
            "turnover_notional": _json_number(self._turnover_notional),
            "execution_costs": {
                "fees": _json_number(total_fees),
                "slippage": _json_number(self._slippage_cost),
                "total": _json_number(total_execution_cost),
            },
        }
