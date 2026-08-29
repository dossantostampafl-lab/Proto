from datetime import UTC, datetime, timedelta

from apps.api.app.models import MarketSnapshot
from apps.api.app.replay import ReplayFrameInput, ReplaySession, ReplayStartRequest


def _request(frame_count: int = 8) -> ReplayStartRequest:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    frames = [
        ReplayFrameInput(
            timestamp=start + timedelta(seconds=index),
            snapshot=MarketSnapshot(
                symbol="BTC",
                market_id="btc-replay-chaos",
                bid=100_000 + index,
                ask=100_001 + index,
                market_probability=0.5,
            ),
        )
        for index in range(frame_count)
    ]
    return ReplayStartRequest(frames=frames, speed="10x")


def test_checkpoint_restore_replays_identical_next_frame() -> None:
    session = ReplaySession()
    session.start(_request())

    for _ in range(3):
        assert session.step() is not None

    checkpoint = session.checkpoint()
    expected = session.step()
    assert expected is not None

    restored = session.restore(checkpoint)
    assert restored["cursor"] == 3
    assert restored["speed"] == "10x"
    assert restored["paused"] is False

    replayed = session.step()
    assert replayed == expected


def test_checkpoint_restore_preserves_pause_and_last_timestamp() -> None:
    session = ReplaySession()
    session.start(_request())
    first = session.step()
    assert first is not None
    session.pause()

    checkpoint = session.checkpoint()
    session.step()
    restored = session.restore(checkpoint)

    assert restored["paused"] is True
    assert restored["cursor"] == 1
    assert restored["last_timestamp"] == first.timestamp


def test_restore_at_end_forces_paused_finished_state() -> None:
    session = ReplaySession()
    session.start(_request(frame_count=2))
    session.step()
    session.step()

    checkpoint = session.checkpoint()
    session.restart()
    restored = session.restore(checkpoint)

    assert restored["cursor"] == 2
    assert restored["finished"] is True
    assert restored["paused"] is True


def test_restart_after_partial_progress_is_deterministic() -> None:
    session = ReplaySession()
    session.start(_request())
    original_first = session.step()
    session.step()
    session.step()

    restarted = session.restart()
    replayed_first = session.step()

    assert restarted["cursor"] == 0
    assert replayed_first == original_first
