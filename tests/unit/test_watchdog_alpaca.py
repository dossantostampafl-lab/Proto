"""The watchdog's Alpaca (US, paper) probe: halt-only, the Alpaca executor only, off unless set."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from test_watchdog import EXEC_WATCH, WATCH, FakeSide, floor
from test_watchdog_xp import FakeXp, client, xp_status

from services.watchdog import Checks, Observation, Watchdog

# Tuesday 2026-10-27: 15:00 UTC is 11:00 in New York (open); 22:00 UTC is 18:00 (closed).
IN_SESSION = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
AFTER_HOURS = datetime(2026, 10, 27, 22, 0, tzinfo=UTC)


def observe(at: datetime, alpaca: dict | None) -> Observation:
    return Observation(
        at=at,
        api_health=True,
        floor=floor(),
        market_ready=True,
        alpaca_configured=True,
        alpaca=alpaca,
    )


def test_not_configured_means_not_watched_and_unreachable_is_reported() -> None:
    assert Checks().evaluate_alpaca(Observation(IN_SESSION, True, floor(), True)) == ([], {})
    trips, status = Checks().evaluate_alpaca(observe(IN_SESSION, None))
    assert trips == [] and status == {"alpaca_executor": "unreachable"}


def test_a_divergence_trips_and_staleness_only_in_the_us_session() -> None:
    for at in (IN_SESSION, AFTER_HOURS):
        trips, _ = Checks().evaluate_alpaca(observe(at, xp_status(at, consistent=False)))
        assert trips == ["alpaca_reconciliation_divergence"]
    stale = Checks().evaluate_alpaca(observe(IN_SESSION, xp_status(IN_SESSION, age_minutes=10)))
    assert stale[0] == ["alpaca_reconciliation_stale"]
    late = Checks().evaluate_alpaca(observe(AFTER_HOURS, xp_status(AFTER_HOURS, age_minutes=10)))
    assert late[0] == []


async def test_an_alpaca_divergence_halts_the_alpaca_executor_only() -> None:
    api, ex, alpaca = FakeSide(), FakeSide(executor=True), FakeXp()
    wd = Watchdog(
        watchdog_id="wd-test",
        api=client(api, "http://api"),
        api_token=WATCH,
        executor=client(ex, "http://ex"),
        executor_token=EXEC_WATCH,
        alpaca_executor=client(alpaca, "http://alpaca"),
        alpaca_executor_token=EXEC_WATCH,
    )
    alpaca.consistent = False
    result = await wd.cycle()
    assert result.trips == ["alpaca_reconciliation_divergence"]
    assert alpaca.halts[0]["reasons"] == ["alpaca_reconciliation_divergence"]
    assert api.halts == [] and ex.halts == []
    assert api.beats[-1]["checks"]["alpaca_halted_by_watchdog"] == "true"
    await wd.cycle()
    assert len(alpaca.halts) == 1  # still halted: not repeated


def test_alpaca_settings_go_together() -> None:
    from apps.watchdog.settings import WatchdogSettings

    base = {"api_url": "http://api", "api_token": WATCH}
    assert WatchdogSettings(**base).alpaca_executor_url is None
    with pytest.raises(ValidationError):
        WatchdogSettings(**base, alpaca_executor_url="http://10.0.1.5:8101")
    with pytest.raises(ValidationError):
        WatchdogSettings(**base, alpaca_executor_url="http://x", alpaca_executor_token="short")
