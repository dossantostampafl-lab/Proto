from datetime import UTC, datetime

from services.market_data.adapters import (
    CSVReplayAdapter,
    HistoricalReplayAdapter,
    SyntheticAdapter,
)
from services.market_data.core import MarketTick


def test_synthetic_adapter_is_deterministic() -> None:
    first = list(
        SyntheticAdapter(
            symbol="BTC",
            start_price=60_000,
            seed=11,
            count=3,
        ).stream()
    )
    second = list(
        SyntheticAdapter(
            symbol="BTC",
            start_price=60_000,
            seed=11,
            count=3,
        ).stream()
    )

    assert first == second
    assert [tick.sequence for tick in first] == [0, 1, 2]
    assert all(tick.bid < tick.ask for tick in first)


def test_csv_replay_adapter_parses_contract() -> None:
    csv_text = (
        "timestamp,venue,symbol,bid,ask,last,volume,bid_size,ask_size,sequence\n"
        "2026-08-29T00:00:00+00:00,replay,ETH,3000,3001,3000.5,4,2,3,7\n"
    )

    ticks = list(CSVReplayAdapter(csv_text).stream())

    assert len(ticks) == 1
    assert ticks[0].symbol == "ETH"
    assert ticks[0].sequence == 7
    assert ticks[0].mid == 3000.5


def test_historical_adapter_orders_timestamp_then_sequence() -> None:
    base = datetime(2026, 8, 29, tzinfo=UTC)
    late = MarketTick(
        timestamp=base,
        venue="replay",
        symbol="SOL",
        bid=140,
        ask=141,
        last=140.5,
        volume=1,
        bid_size=1,
        ask_size=1,
        sequence=2,
    )
    early = late.model_copy(update={"sequence": 1})

    ticks = list(HistoricalReplayAdapter([late, early]).stream())

    assert [tick.sequence for tick in ticks] == [1, 2]
