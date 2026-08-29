from datetime import UTC, datetime, timedelta
from random import Random

from apps.api.app.models import MarketSnapshot
from apps.api.app.replay import HistoricalReplay, ReplayFrame


def _frame(index: int) -> ReplayFrame:
    return ReplayFrame(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(milliseconds=index),
        snapshot=MarketSnapshot(
            symbol="BTC",
            market_id="btc-chaos-replay",
            bid=60_000 + index,
            ask=60_001 + index,
            market_probability=0.5,
        ),
    )


def test_replay_canonicalizes_scrambled_input_deterministically() -> None:
    frames = [_frame(index) for index in range(1_000)]
    scrambled = list(frames)
    Random(42).shuffle(scrambled)

    replay = HistoricalReplay(scrambled)
    emitted = replay.run_all()

    assert emitted == frames
    assert replay.finished is True
    assert replay.cursor == len(frames)


def test_replay_seek_and_restart_recover_after_scrambled_input() -> None:
    frames = [_frame(index) for index in range(100)]
    scrambled = list(frames)
    Random(7).shuffle(scrambled)
    replay = HistoricalReplay(scrambled)

    replay.seek(50)
    assert replay.next() == frames[50]
    replay.reset()
    assert replay.next() == frames[0]
