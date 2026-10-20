"""The watchdog's XP (B3) probe: reported when unreachable, halts the XP executor only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError
from test_watchdog import EXEC_WATCH, WATCH, FakeSide, floor

from services.watchdog import Checks, Observation, Watchdog

# Tuesday 2026-10-13: 15:00 UTC is 12:00 in Sao Paulo (session open), 23:00 UTC is 20:00 (closed).
IN_SESSION = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
AFTER_HOURS = datetime(2026, 10, 13, 23, 0, tzinfo=UTC)


def xp_status(at: datetime, *, consistent: bool = True, age_minutes: int = 0) -> dict:
    return {
        "ready": True,
        "halted": None,
        "consistent": consistent,
        "last_reconcile_at": (at - timedelta(minutes=age_minutes)).isoformat(),
    }


def observe(at: datetime, xp: dict | None) -> Observation:
    return Observation(
        at=at, api_health=True, floor=floor(), market_ready=True, xp_configured=True, xp=xp
    )


def test_an_unreachable_node_is_reported_never_tripped() -> None:
    checks = Checks()
    for _ in range(100):  # a PC switched off for the night
        trips, status = checks.evaluate_xp(observe(IN_SESSION, None))
    assert trips == [] and status == {"xp_executor": "unreachable"}
    assert checks.evaluate_xp(Observation(IN_SESSION, True, floor(), True)) == ([], {})


def test_a_divergence_trips_at_any_hour() -> None:
    for at in (IN_SESSION, AFTER_HOURS):
        trips, _ = Checks().evaluate_xp(observe(at, xp_status(at, consistent=False)))
        assert trips == ["xp_reconciliation_divergence"]


def test_a_stale_reconciliation_trips_only_during_the_b3_session() -> None:
    def trips(at: datetime, age: int) -> list[str]:
        return Checks().evaluate_xp(observe(at, xp_status(at, age_minutes=age)))[0]

    assert trips(IN_SESSION, 10) == ["xp_reconciliation_stale"]
    assert trips(AFTER_HOURS, 10) == []
    assert trips(IN_SESSION, 0) == []


class FakeXp(FakeSide):
    def __init__(self) -> None:
        super().__init__(executor=True)
        self.consistent = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/watchdog/status" and self.up:
            self.calls.append((request.method, request.url.path))
            assert request.headers["Authorization"] == f"Bearer {EXEC_WATCH}"
            return httpx.Response(
                200,
                json={
                    **xp_status(datetime.now(UTC), consistent=self.consistent),
                    "halted": "x" if self.halted else None,
                },
            )
        return super().handler(request)


def client(side: FakeSide, base: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base, transport=httpx.MockTransport(side.handler))


def watchdog(api: FakeSide, ex: FakeSide, xp: FakeXp) -> Watchdog:
    return Watchdog(
        watchdog_id="wd-test",
        api=client(api, "http://api"),
        api_token=WATCH,
        executor=client(ex, "http://ex"),
        executor_token=EXEC_WATCH,
        xp_executor=client(xp, "http://xp"),
        xp_executor_token=EXEC_WATCH,
    )


async def test_an_xp_divergence_halts_the_xp_executor_only() -> None:
    api, ex, xp = FakeSide(), FakeSide(executor=True), FakeXp()
    wd = watchdog(api, ex, xp)
    xp.consistent = False
    result = await wd.cycle()
    assert result.trips == ["xp_reconciliation_divergence"]
    assert xp.halts[0]["reasons"] == ["xp_reconciliation_divergence"]
    assert api.halts == [] and ex.halts == []  # Binance and the API keep running
    assert not result.api_halted and not result.executor_halted
    beat = api.beats[-1]
    assert beat["checks"]["xp_halted_by_watchdog"] == "true"
    assert "xp_reconciliation_divergence" in beat["trips"]

    await wd.cycle()  # still halted: not repeated
    assert len(xp.halts) == 1


async def test_a_switched_off_pc_halts_nothing() -> None:
    api, ex, xp = FakeSide(), FakeSide(executor=True), FakeXp()
    wd = watchdog(api, ex, xp)
    xp.up = False
    for _ in range(50):
        result = await wd.cycle()
    assert result.trips == [] and api.halts == [] and ex.halts == [] and xp.halts == []
    assert result.status["xp_executor"] == "unreachable"


def test_xp_settings_go_together_and_need_a_long_token() -> None:
    from apps.watchdog.settings import WatchdogSettings

    base = {"api_url": "http://api", "api_token": WATCH}
    url = "http://100.64.0.1:8101"
    with pytest.raises(ValidationError):
        WatchdogSettings(**base, xp_executor_url=url)
    with pytest.raises(ValidationError):
        WatchdogSettings(**base, xp_executor_url=url, xp_executor_token="short")
    ok = WatchdogSettings(**base, xp_executor_url=url, xp_executor_token=WATCH)
    assert ok.xp_executor_url == url
