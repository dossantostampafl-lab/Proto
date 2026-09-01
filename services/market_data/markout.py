from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Literal

from .l2_corpus_replay import PublicL2ReplaySnapshot

Side = Literal["BUY", "SELL"]
DEFAULT_MARKOUT_HORIZONS_MS = (100, 500, 1_000, 5_000, 30_000)


@dataclass(frozen=True, slots=True)
class FillObservation:
    fill_id: str
    side: Side
    fill_price: float
    filled_at: datetime
    connection_generation: int
    asset: str

    def __post_init__(self) -> None:
        if not self.fill_id.strip():
            raise ValueError("fill_id must not be blank")
        if self.side not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")
        if self.fill_price <= 0.0:
            raise ValueError("fill_price must be positive")
        if self.filled_at.tzinfo is None or self.filled_at.utcoffset() is None:
            raise ValueError("filled_at must be timezone-aware")
        if self.connection_generation <= 0:
            raise ValueError("connection_generation must be positive")
        if not self.asset.strip():
            raise ValueError("asset must not be blank")


@dataclass(frozen=True, slots=True)
class MarkoutPoint:
    horizon_ms: int
    observed_at: datetime | None
    future_mid: float | None
    markout_bps: float | None
    adverse_selection_bps: float | None


@dataclass(frozen=True, slots=True)
class FillMarkout:
    fill_id: str
    side: Side
    fill_price: float
    entry_mid: float
    spread_capture_bps: float
    points: tuple[MarkoutPoint, ...]


@dataclass(frozen=True, slots=True)
class MarkoutSummary:
    horizon_ms: int
    observation_count: int
    mean_markout_bps: float | None
    median_markout_bps: float | None
    mean_adverse_selection_bps: float | None
    adverse_selection_fraction: float | None


def _signed_move_bps(side: Side, start: float, end: float) -> float:
    direction = 1.0 if side == "BUY" else -1.0
    return direction * ((end - start) / start) * 10_000.0


def _spread_capture_bps(side: Side, fill_price: float, entry_mid: float) -> float:
    if side == "BUY":
        return ((entry_mid - fill_price) / fill_price) * 10_000.0
    return ((fill_price - entry_mid) / fill_price) * 10_000.0


def _eligible_snapshots(
    snapshots: tuple[PublicL2ReplaySnapshot, ...],
    fill: FillObservation,
) -> tuple[PublicL2ReplaySnapshot, ...]:
    return tuple(
        item
        for item in snapshots
        if item.connection_generation == fill.connection_generation
        and item.snapshot.asset == fill.asset
        and item.snapshot.observed_at >= fill.filled_at
    )


def _first_snapshot_at_or_after(
    snapshots: tuple[PublicL2ReplaySnapshot, ...],
    target: datetime,
) -> PublicL2ReplaySnapshot | None:
    return next((item for item in snapshots if item.snapshot.observed_at >= target), None)


def compute_fill_markout(
    snapshots: tuple[PublicL2ReplaySnapshot, ...],
    fill: FillObservation,
    *,
    horizons_ms: tuple[int, ...] = DEFAULT_MARKOUT_HORIZONS_MS,
) -> FillMarkout:
    """Measure post-fill mid-price movement without crossing reconnect boundaries."""

    if not horizons_ms or any(value <= 0 for value in horizons_ms):
        raise ValueError("markout horizons must contain positive milliseconds")
    if tuple(sorted(set(horizons_ms))) != horizons_ms:
        raise ValueError("markout horizons must be unique and strictly increasing")

    eligible = _eligible_snapshots(snapshots, fill)
    entry = _first_snapshot_at_or_after(eligible, fill.filled_at)
    if entry is None:
        raise ValueError("no L2 snapshot is available at or after the fill")

    entry_mid = entry.snapshot.mid_price
    points: list[MarkoutPoint] = []
    for horizon_ms in horizons_ms:
        target = fill.filled_at + timedelta(milliseconds=horizon_ms)
        future = _first_snapshot_at_or_after(eligible, target)
        if future is None:
            points.append(
                MarkoutPoint(
                    horizon_ms=horizon_ms,
                    observed_at=None,
                    future_mid=None,
                    markout_bps=None,
                    adverse_selection_bps=None,
                )
            )
            continue

        markout_bps = _signed_move_bps(fill.side, fill.fill_price, future.snapshot.mid_price)
        points.append(
            MarkoutPoint(
                horizon_ms=horizon_ms,
                observed_at=future.snapshot.observed_at,
                future_mid=future.snapshot.mid_price,
                markout_bps=markout_bps,
                adverse_selection_bps=max(0.0, -markout_bps),
            )
        )

    return FillMarkout(
        fill_id=fill.fill_id,
        side=fill.side,
        fill_price=fill.fill_price,
        entry_mid=entry_mid,
        spread_capture_bps=_spread_capture_bps(fill.side, fill.fill_price, entry_mid),
        points=tuple(points),
    )


def summarize_markouts(
    markouts: tuple[FillMarkout, ...],
) -> tuple[MarkoutSummary, ...]:
    if not markouts:
        return ()

    horizons = tuple(point.horizon_ms for point in markouts[0].points)
    if any(tuple(point.horizon_ms for point in item.points) != horizons for item in markouts):
        raise ValueError("all markouts must use identical horizons")

    summaries: list[MarkoutSummary] = []
    for index, horizon_ms in enumerate(horizons):
        values = [
            item.points[index].markout_bps
            for item in markouts
            if item.points[index].markout_bps is not None
        ]
        resolved = [float(value) for value in values]
        if not resolved:
            summaries.append(
                MarkoutSummary(
                    horizon_ms=horizon_ms,
                    observation_count=0,
                    mean_markout_bps=None,
                    median_markout_bps=None,
                    mean_adverse_selection_bps=None,
                    adverse_selection_fraction=None,
                )
            )
            continue

        adverse = [max(0.0, -value) for value in resolved]
        summaries.append(
            MarkoutSummary(
                horizon_ms=horizon_ms,
                observation_count=len(resolved),
                mean_markout_bps=sum(resolved) / len(resolved),
                median_markout_bps=median(resolved),
                mean_adverse_selection_bps=sum(adverse) / len(adverse),
                adverse_selection_fraction=(
                    sum(1 for value in resolved if value < 0.0) / len(resolved)
                ),
            )
        )
    return tuple(summaries)
