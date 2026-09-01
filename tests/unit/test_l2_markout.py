from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from services.market_data import (
    BookLevel,
    DataSource,
    FillObservation,
    OrderBookSnapshot,
    PublicL2ReplaySnapshot,
    compute_fill_markout,
    summarize_markouts,
)

BASE = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _snapshot(
    offset_ms: int,
    mid: float,
    *,
    generation: int = 1,
    asset: str = "BTC",
) -> PublicL2ReplaySnapshot:
    observed_at = BASE + timedelta(milliseconds=offset_ms)
    return PublicL2ReplaySnapshot(
        record_index=offset_ms,
        connection_generation=generation,
        wire_sequence=offset_ms + 1,
        snapshot=OrderBookSnapshot(
            market_id=f"{asset}-USD",
            asset=asset,  # type: ignore[arg-type]
            bids=(BookLevel(price=mid - 0.5, size=1.0),),
            asks=(BookLevel(price=mid + 0.5, size=1.0),),
            observed_at=observed_at,
            source=DataSource.HISTORICAL_REPLAY,
        ),
    )


def test_buy_markout_measures_signed_future_mid_and_spread_capture() -> None:
    snapshots = (
        _snapshot(0, 100.0),
        _snapshot(100, 100.2),
        _snapshot(500, 99.8),
        _snapshot(1_000, 100.5),
    )
    fill = FillObservation(
        fill_id="fill-1",
        side="BUY",
        fill_price=99.5,
        filled_at=BASE,
        connection_generation=1,
        asset="BTC",
    )

    result = compute_fill_markout(snapshots, fill, horizons_ms=(100, 500, 1_000))

    assert result.entry_mid == pytest.approx(100.0)
    assert result.spread_capture_bps == pytest.approx(50.251256, rel=1e-6)
    assert result.points[0].markout_bps == pytest.approx(70.351758, rel=1e-6)
    assert result.points[0].adverse_selection_bps == 0.0
    assert result.points[1].markout_bps == pytest.approx(30.150754, rel=1e-6)
    assert result.points[2].markout_bps == pytest.approx(100.502513, rel=1e-6)


def test_sell_markout_inverts_direction_and_flags_adverse_selection() -> None:
    snapshots = (_snapshot(0, 100.0), _snapshot(500, 101.0))
    fill = FillObservation(
        fill_id="fill-2",
        side="SELL",
        fill_price=100.5,
        filled_at=BASE,
        connection_generation=1,
        asset="BTC",
    )

    result = compute_fill_markout(snapshots, fill, horizons_ms=(500,))

    assert result.spread_capture_bps == pytest.approx(49.751244, rel=1e-6)
    assert result.points[0].markout_bps == pytest.approx(-49.751244, rel=1e-6)
    assert result.points[0].adverse_selection_bps == pytest.approx(49.751244, rel=1e-6)


def test_markout_never_crosses_connection_generation() -> None:
    snapshots = (
        _snapshot(0, 100.0, generation=1),
        _snapshot(100, 100.1, generation=1),
        _snapshot(500, 110.0, generation=2),
    )
    fill = FillObservation(
        fill_id="fill-3",
        side="BUY",
        fill_price=99.5,
        filled_at=BASE,
        connection_generation=1,
        asset="BTC",
    )

    result = compute_fill_markout(snapshots, fill, horizons_ms=(100, 500))

    assert result.points[0].markout_bps is not None
    assert result.points[1].markout_bps is None
    assert result.points[1].future_mid is None


def test_markout_uses_first_snapshot_at_or_after_horizon() -> None:
    snapshots = (
        _snapshot(0, 100.0),
        _snapshot(120, 100.25),
        _snapshot(700, 100.75),
    )
    fill = FillObservation(
        fill_id="fill-4",
        side="BUY",
        fill_price=100.0,
        filled_at=BASE,
        connection_generation=1,
        asset="BTC",
    )

    result = compute_fill_markout(snapshots, fill, horizons_ms=(100, 500))

    assert result.points[0].observed_at == BASE + timedelta(milliseconds=120)
    assert result.points[1].observed_at == BASE + timedelta(milliseconds=700)


def test_summary_reports_adverse_selection_fraction() -> None:
    snapshots = (
        _snapshot(0, 100.0),
        _snapshot(500, 101.0),
    )
    buy = compute_fill_markout(
        snapshots,
        FillObservation(
            fill_id="buy",
            side="BUY",
            fill_price=100.0,
            filled_at=BASE,
            connection_generation=1,
            asset="BTC",
        ),
        horizons_ms=(500,),
    )
    sell = compute_fill_markout(
        snapshots,
        FillObservation(
            fill_id="sell",
            side="SELL",
            fill_price=100.0,
            filled_at=BASE,
            connection_generation=1,
            asset="BTC",
        ),
        horizons_ms=(500,),
    )

    summary = summarize_markouts((buy, sell))[0]

    assert summary.observation_count == 2
    assert summary.mean_markout_bps == pytest.approx(0.0)
    assert summary.median_markout_bps == pytest.approx(0.0)
    assert summary.mean_adverse_selection_bps == pytest.approx(50.0)
    assert summary.adverse_selection_fraction == 0.5


def test_invalid_horizons_and_missing_entry_fail_closed() -> None:
    fill = FillObservation(
        fill_id="fill-5",
        side="BUY",
        fill_price=100.0,
        filled_at=BASE,
        connection_generation=1,
        asset="BTC",
    )

    with pytest.raises(ValueError, match="positive milliseconds"):
        compute_fill_markout((_snapshot(0, 100.0),), fill, horizons_ms=(0,))
    with pytest.raises(ValueError, match="unique and strictly increasing"):
        compute_fill_markout((_snapshot(0, 100.0),), fill, horizons_ms=(500, 100))
    with pytest.raises(ValueError, match="no L2 snapshot"):
        compute_fill_markout((_snapshot(0, 100.0, generation=2),), fill)
