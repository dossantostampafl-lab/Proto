from datetime import UTC, datetime, timedelta

import pytest

from apps.api.app.models import MarketSnapshot
from apps.api.app.replay import ReplayFrameInput, ReplaySession, ReplayStartRequest


def _snapshot(symbol: str, price: float) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        market_id=f"{symbol.lower()}-replay",
        bid=price - 1,
        ask=price + 1,
        bid_size=2.0,
        ask_size=1.0,
        volatility=0.2,
        imbalance=0.25,
        market_probability=0.55,
    )


def _request() -> ReplayStartRequest:
    now = datetime.now(UTC)
    return ReplayStartRequest(
        speed="10x",
        frames=[
            ReplayFrameInput(timestamp=now + timedelta(seconds=2), snapshot=_snapshot("BTC", 102)),
            ReplayFrameInput(timestamp=now, snapshot=_snapshot("BTC", 100)),
            ReplayFrameInput(timestamp=now + timedelta(seconds=1), snapshot=_snapshot("BTC", 101)),
        ],
    )


def test_replay_session_orders_frames_and_tracks_cursor() -> None:
    session = ReplaySession()
    status = session.start(_request())

    assert status["active"] is True
    assert status["paused"] is False
    assert status["speed"] == "10x"
    assert status["cursor"] == 0
    assert status["total_frames"] == 3

    first = session.step()
    second = session.step()

    assert first is not None and first.snapshot.bid == 99
    assert second is not None and second.snapshot.bid == 100
    assert session.status()["cursor"] == 2


def test_replay_session_pauses_when_finished_and_can_restart() -> None:
    session = ReplaySession()
    session.start(_request())

    assert session.step() is not None
    assert session.step() is not None
    assert session.step() is not None
    assert session.status()["finished"] is True
    assert session.status()["paused"] is True
    assert session.step() is None

    restarted = session.restart()
    assert restarted["cursor"] == 0
    assert restarted["paused"] is False
    assert restarted["finished"] is False


def test_replay_session_requires_start_before_controls() -> None:
    session = ReplaySession()

    with pytest.raises(RuntimeError, match="has not been started"):
        session.pause()
    with pytest.raises(RuntimeError, match="has not been started"):
        session.resume()
    with pytest.raises(RuntimeError, match="has not been started"):
        session.step()


def test_finished_replay_cannot_resume_without_restart() -> None:
    session = ReplaySession()
    session.start(_request())
    session.step()
    session.step()
    session.step()

    with pytest.raises(RuntimeError, match="already finished"):
        session.resume()


def test_replay_seek_is_bounded_deterministic_and_pauses() -> None:
    session = ReplaySession()
    session.start(_request())

    status = session.seek(2)

    assert status["cursor"] == 2
    assert status["paused"] is True
    assert status["last_timestamp"] is not None
    assert session.step() is not None
    assert session.status()["finished"] is True

    with pytest.raises(RuntimeError, match="exceeds total frames"):
        session.seek(4)


def test_replay_speed_can_change_without_moving_cursor() -> None:
    session = ReplaySession()
    session.start(_request())

    status = session.set_speed("100x")

    assert status["speed"] == "100x"
    assert status["cursor"] == 0

