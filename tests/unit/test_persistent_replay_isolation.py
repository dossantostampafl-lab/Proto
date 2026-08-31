from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from apps.api.app import main as api_main
from apps.api.app.models import KillSwitchState, MarketSnapshot
from apps.api.app.replay import ReplayFrameInput, ReplaySeekRequest, ReplayStartRequest


class FakeJournal:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.rotations = 0

    async def start_new_session(self) -> str:
        if self.fail:
            raise RuntimeError("database unavailable")
        self.rotations += 1
        return f"session-{self.rotations}"


def _request() -> ReplayStartRequest:
    timestamp = datetime(2024, 1, 2, 15, 30, tzinfo=UTC)
    return ReplayStartRequest(
        frames=[
            ReplayFrameInput(
                timestamp=timestamp,
                snapshot=MarketSnapshot(
                    symbol="BTC",
                    market_id="btc-replay-isolation",
                    bid=99.0,
                    ask=101.0,
                    bid_size=10.0,
                    ask_size=10.0,
                ),
            )
        ]
    )


@pytest.fixture(autouse=True)
def reset_replay_runtime(monkeypatch: pytest.MonkeyPatch):
    api_main.replay_session.reset()
    api_main.portfolio.reset()
    api_main.runtime.kill_switch = KillSwitchState.ARMED
    api_main.runtime.running = False
    yield
    api_main.replay_session.reset()
    api_main.portfolio.reset()


@pytest.mark.asyncio
async def test_replay_timeline_changes_rotate_persistent_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = FakeJournal()
    monkeypatch.setattr(api_main, "persistent_journal", journal)

    await api_main.replay_start(_request())
    assert journal.rotations == 1

    await api_main.replay_restart()
    assert journal.rotations == 2

    await api_main.replay_seek(ReplaySeekRequest(cursor=0))
    assert journal.rotations == 3


@pytest.mark.asyncio
async def test_invalid_seek_does_not_rotate_persistent_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = FakeJournal()
    monkeypatch.setattr(api_main, "persistent_journal", journal)
    await api_main.replay_start(_request())
    assert journal.rotations == 1

    with pytest.raises(HTTPException) as caught:
        await api_main.replay_seek(ReplaySeekRequest(cursor=2))

    assert caught.value.status_code == 409
    assert journal.rotations == 1


@pytest.mark.asyncio
async def test_persistence_failure_is_fail_closed_before_timeline_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = FakeJournal(fail=True)
    monkeypatch.setattr(api_main, "persistent_journal", journal)

    with pytest.raises(HTTPException) as caught:
        await api_main.replay_start(_request())

    assert caught.value.status_code == 503
    assert api_main.replay_session.active is False
    assert api_main.runtime.running is False
