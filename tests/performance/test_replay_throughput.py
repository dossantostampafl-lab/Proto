from datetime import UTC, datetime, timedelta
from time import perf_counter

from apps.api.app.models import MarketSnapshot
from apps.api.app.replay import HistoricalReplay, ReplayFrame


def test_replay_processes_twenty_thousand_frames_within_ci_budget() -> None:
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    frames = [
        ReplayFrame(
            timestamp=started_at + timedelta(microseconds=index),
            snapshot=MarketSnapshot(
                symbol="ETH",
                market_id="eth-performance-replay",
                bid=3_000 + index * 0.001,
                ask=3_001 + index * 0.001,
                market_probability=0.5,
            ),
        )
        for index in range(20_000)
    ]

    started = perf_counter()
    replay = HistoricalReplay(list(reversed(frames)))
    emitted = replay.run_all()
    elapsed = perf_counter() - started

    assert len(emitted) == 20_000
    assert emitted[0].timestamp == frames[0].timestamp
    assert emitted[-1].timestamp == frames[-1].timestamp
    assert replay.finished is True
    assert elapsed < 5.0
