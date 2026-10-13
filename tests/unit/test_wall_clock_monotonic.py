from datetime import UTC, datetime, timedelta

from apps.api.app import app_state


def test_wall_clock_never_steps_backwards(monkeypatch) -> None:
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    readings = iter([start, start - timedelta(seconds=2), start + timedelta(seconds=1)])

    class SteppingClock(datetime):
        @classmethod
        def now(cls, tz=None):  # mirrors datetime.now(tz)
            return next(readings)

    # Both are restored after the test, so other tests keep the real clock.
    monkeypatch.setattr(app_state, "datetime", SteppingClock)
    monkeypatch.setattr(app_state, "_last_wall_clock", datetime.min.replace(tzinfo=UTC))

    first = app_state.non_decreasing_utc_now()
    after_backward_step = app_state.non_decreasing_utc_now()
    after_recovery = app_state.non_decreasing_utc_now()

    assert first == start
    assert after_backward_step == start  # an NTP step back is absorbed
    assert after_recovery == start + timedelta(seconds=1)
