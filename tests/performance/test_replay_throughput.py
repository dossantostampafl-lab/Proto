from datetime import UTC, datetime, timedelta
from time import perf_counter

from apps.api.app.models import MarketSnapshot
from apps.api.app.replay import ReplayFrameInput, ReplaySession, ReplayStartRequest


def test_replay_steps_twenty_thousand_frames_without_pathological_slowdown() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    frame_count = 20_000
    frames = [
        ReplayFrameInput(
            timestamp=start + timedelta(milliseconds=index),
            snapshot=MarketSnapshot(
                symbol="BTC",
                market_id="btc-performance-replay",
                bid=100_000 + index * 0.01,
                ask=100_001 + index * 0.01,
                market_probability=0.5,
            ),
        )
        for index in range(frame_count)
    ]
    session = ReplaySession()
    session.start(ReplayStartRequest(frames=frames, speed="MAX"))

    started = perf_counter()
    processed = 0
    while session.step() is not None:
        processed += 1
    elapsed = perf_counter() - started

    assert processed == frame_count
    assert session.status()["finished"] is True
    assert elapsed < 5.0
