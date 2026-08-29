from datetime import UTC, datetime, timedelta

from apps.api.app.models import MarketSnapshot
from apps.api.app.replay import ReplayFrameInput, ReplaySession, ReplayStartRequest


def _request(*, speed: str, count: int) -> ReplayStartRequest:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return ReplayStartRequest(
        speed=speed,
        frames=[
            ReplayFrameInput(
                timestamp=base + timedelta(seconds=index),
                snapshot=MarketSnapshot(
                    symbol="BTC",
                    market_id="btc-replay",
                    bid=100 + index,
                    ask=101 + index,
                ),
            )
            for index in range(count)
        ],
    )


def test_advance_uses_configured_speed_as_deterministic_batch_size() -> None:
    session = ReplaySession()
    session.start(_request(speed="5x", count=12))

    frames = session.advance()

    assert len(frames) == 5
    assert session.status()["cursor"] == 5
    assert session.status()["paused"] is False


def test_advance_stops_at_dataset_end_and_pauses() -> None:
    session = ReplaySession()
    session.start(_request(speed="10x", count=3))

    frames = session.advance()

    assert len(frames) == 3
    assert session.status()["cursor"] == 3
    assert session.status()["finished"] is True
    assert session.status()["paused"] is True


def test_advance_does_not_move_while_paused() -> None:
    session = ReplaySession()
    session.start(_request(speed="5x", count=8))
    session.pause()

    assert session.advance() == []
    assert session.status()["cursor"] == 0


def test_max_speed_is_bounded_per_batch() -> None:
    session = ReplaySession()
    session.start(_request(speed="MAX", count=150))

    frames = session.advance()

    assert len(frames) == 100
    assert session.status()["cursor"] == 100
    assert session.status()["finished"] is False
