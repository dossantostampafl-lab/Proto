"""Independent watchdog: checks, the halt loop, its halt-only tokens and the coverage evidence."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fake_binance import FakeBinance
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from services.watchdog import API_TOKEN_HEADER, Checks, Limits, Observation, Watchdog

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
WATCH = "w" * 32
EXEC_WATCH = "x" * 32


def account(equity="10000", start="10000", peak="10000") -> dict:
    return {
        "equity": equity,
        "start_of_day_equity": start,
        "peak_equity": peak,
        "gross_exposure": "0",
        "positions": {},
    }


def floor(**overrides) -> dict:
    values = {
        "journal": "VALID",
        "accounts": {"USDT": account()},
        "controls": {"global_kill": False},
        "pipeline": {"paper_execution_enabled": True, "execution_backend": "paper"},
    }
    values.update(overrides)
    return values


def obs(**overrides) -> Observation:
    values = {"at": NOW, "api_health": True, "floor": floor(), "market_ready": True}
    values.update(overrides)
    return Observation(**values)


# --- checks ------------------------------------------------------------------------


def test_healthy_observation_trips_nothing() -> None:
    trips, status = Checks().evaluate(obs())
    assert trips == []
    assert set(status.values()) == {"ok"}


@pytest.mark.parametrize(
    ("observation", "reason"),
    [
        (obs(floor=floor(accounts={"USDT": account(equity="9900")})), "daily_loss:USDT"),
        (
            obs(floor=floor(accounts={"USDT": account(equity="9499", start="9500")})),
            "drawdown:USDT",
        ),
        (obs(floor=floor(journal="CORRUPTED")), "journal_corrupted"),
    ],
)
def test_limit_and_integrity_breaches_trip_at_once(observation, reason) -> None:
    trips, _ = Checks().evaluate(observation)
    assert reason in trips


def test_tighter_limits_trip_earlier_and_looser_ones_are_refused() -> None:
    near = obs(floor=floor(accounts={"USDT": account(equity="9950")}))
    assert Checks().evaluate(near)[0] == []
    assert Checks(Limits(daily_loss_fraction=Decimal("0.5"))).evaluate(near)[0] == [
        "daily_loss:USDT"
    ]
    with pytest.raises(ValueError, match="only tighten"):
        Limits(daily_loss_fraction=Decimal("1.5"))


def test_outages_trip_only_when_sustained() -> None:
    checks = Checks()
    down = obs(api_health=False, floor=None, market_ready=None)
    assert checks.evaluate(down)[0] == []
    assert checks.evaluate(down)[0] == []
    assert checks.evaluate(down)[0] == ["api_unreachable"]
    assert checks.evaluate(obs())[0] == []  # one good cycle resets the count

    unreadable = obs(floor=floor(accounts={"USDT": None}))
    checks = Checks()
    assert [checks.evaluate(unreadable)[0] for _ in range(3)][-1] == ["account_unavailable:USDT"]


def test_stale_market_data_matters_only_while_executing() -> None:
    idle = obs(
        floor=floor(pipeline={"paper_execution_enabled": False, "execution_backend": "paper"}),
        market_ready=False,
    )
    checks = Checks()
    assert all(checks.evaluate(idle)[0] == [] for _ in range(10))
    checks = Checks()
    stale = obs(market_ready=False)
    results = [checks.evaluate(stale)[0] for _ in range(6)]
    assert results[-2] == [] and results[-1] == ["market_data_stale"]


def test_executor_divergence_and_stale_reconciliation_trip() -> None:
    fresh = {
        "ready": True,
        "halted": None,
        "consistent": True,
        "last_reconcile_at": NOW.isoformat(),
    }
    assert Checks().evaluate(obs(executor_configured=True, executor=fresh))[0] == []
    diverged = dict(fresh, consistent=False)
    assert Checks().evaluate(obs(executor_configured=True, executor=diverged))[0] == [
        "reconciliation_divergence"
    ]
    old = dict(fresh, last_reconcile_at=(NOW - timedelta(minutes=5)).isoformat())
    assert Checks().evaluate(obs(executor_configured=True, executor=old))[0] == [
        "reconciliation_stale"
    ]
    checks = Checks()
    gone = obs(executor_configured=True, executor=None)
    assert [checks.evaluate(gone)[0] for _ in range(3)][-1] == ["executor_unreachable"]


# --- the loop against fake API and executor ----------------------------------------


class FakeSide:
    """A fake API or executor that records what the watchdog asks of it."""

    def __init__(self, *, executor: bool = False) -> None:
        self.executor = executor
        self.up = True
        self.floor = floor()
        self.halted = False
        self.calls: list[tuple[str, str]] = []
        self.halts: list[dict] = []
        self.beats: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if not self.up:
            raise httpx.ConnectError("down")
        path = request.url.path
        if self.executor:
            assert request.headers["Authorization"] == f"Bearer {EXEC_WATCH}"
            if path == "/v1/watchdog/status":
                return httpx.Response(
                    200,
                    json={
                        "ready": True,
                        "halted": "x" if self.halted else None,
                        "consistent": True,
                        "last_reconcile_at": datetime.now(UTC).isoformat(),
                    },
                )
            if path == "/v1/watchdog/halt":
                self.halted = True
                self.halts.append(json.loads(request.content))
                return httpx.Response(200, json={"halted": "watchdog"})
            return httpx.Response(404)
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/v1/floor/state":
            return httpx.Response(200, json=self.floor)
        if path == "/live/ready":
            return httpx.Response(200, json={"status": "ready"})
        assert request.headers[API_TOKEN_HEADER] == WATCH
        body = json.loads(request.content)
        if path == "/v1/watchdog/halt":
            self.floor["controls"]["global_kill"] = True
            self.halts.append(body)
            return httpx.Response(200, json={"controls": {"global_kill": True}})
        if path == "/v1/watchdog/heartbeat":
            self.beats.append(body)
            return httpx.Response(200, json={"accepted": True})
        return httpx.Response(404)


def watchdog(api: FakeSide, ex: FakeSide | None = None) -> Watchdog:
    return Watchdog(
        watchdog_id="wd-test",
        api=httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(api.handler)),
        api_token=WATCH,
        executor=httpx.AsyncClient(base_url="http://ex", transport=httpx.MockTransport(ex.handler))
        if ex
        else None,
        executor_token=EXEC_WATCH if ex else None,
    )


async def test_healthy_cycles_only_send_heartbeats() -> None:
    api, ex = FakeSide(), FakeSide(executor=True)
    wd = watchdog(api, ex)
    for _ in range(3):
        result = await wd.cycle()
    assert result.trips == [] and api.halts == [] and ex.halts == []
    assert len(api.beats) == 3 and api.beats[-1]["watchdog_id"] == "wd-test"


async def test_a_breach_halts_both_sides_once_and_again_after_a_premature_release() -> None:
    api, ex = FakeSide(), FakeSide(executor=True)
    wd = watchdog(api, ex)
    api.floor["accounts"]["USDT"] = account(equity="9890")
    first = await wd.cycle()
    assert first.trips == ["daily_loss:USDT"] and first.api_halted and first.executor_halted
    assert api.halts[0]["reasons"] == ["daily_loss:USDT"] and len(ex.halts) == 1

    await wd.cycle()  # still halted: nothing is repeated
    assert len(api.halts) == 1 and len(ex.halts) == 1

    api.floor["controls"]["global_kill"] = False  # an operator released while the breach persists
    await wd.cycle()
    assert len(api.halts) == 2

    api.floor["accounts"]["USDT"] = account()
    clear = await wd.cycle()
    assert clear.trips == [] and len(api.halts) == 2
    # The watchdog never asks for anything but probes, halts and heartbeats.
    allowed = {
        "/health",
        "/v1/floor/state",
        "/live/ready",
        "/v1/watchdog/halt",
        "/v1/watchdog/heartbeat",
        "/v1/watchdog/status",
    }
    assert {path for _, path in api.calls + ex.calls} <= allowed


async def test_a_garbled_answer_is_an_outage_never_a_crashed_cycle() -> None:
    """A 200 with HTML (a proxy page) used to raise inside `observe`: the cycle died and halted
    nothing, every cycle. It must count as unreadable and trip once sustained."""

    api, ex = FakeSide(), FakeSide(executor=True)
    garbled = httpx.Response(200, text="<html>maintenance</html>")

    def html(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/v1/floor/state", "/v1/watchdog/status"):
            ex.calls.append((request.method, request.url.path))
            return garbled
        return (ex if request.url.host == "ex" else api).handler(request)

    wd = Watchdog(
        watchdog_id="wd-test",
        api=httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(html)),
        api_token=WATCH,
        executor=httpx.AsyncClient(base_url="http://ex", transport=httpx.MockTransport(html)),
        executor_token=EXEC_WATCH,
    )
    results = [await wd.cycle() for _ in range(3)]
    assert results[-1].trips == ["api_unreachable", "executor_unreachable"]
    assert api.halts and ex.halts  # both sides were asked to halt


def test_the_watchdog_never_prints_its_tokens() -> None:
    wd = watchdog(FakeSide(), FakeSide(executor=True))
    assert WATCH not in repr(wd) and EXEC_WATCH not in repr(wd)


def test_a_naive_reconcile_timestamp_is_stale_not_a_crash() -> None:
    naive = datetime.now(UTC).replace(tzinfo=None).isoformat()
    status = {"ready": True, "halted": None, "consistent": True, "last_reconcile_at": naive}
    trips, _ = Checks().evaluate(obs(executor_configured=True, executor=status))
    assert "reconciliation_stale" in trips


async def test_an_unreachable_api_halts_the_executor() -> None:
    api, ex = FakeSide(), FakeSide(executor=True)
    wd = watchdog(api, ex)
    api.up = False
    results = [await wd.cycle() for _ in range(3)]
    assert results[-1].trips == ["api_unreachable"]
    assert results[-1].executor_halted and not results[-1].api_halted
    assert ex.halts[0]["reasons"] == ["api_unreachable"]


# --- API surface ---------------------------------------------------------------------


@pytest.fixture
def api_app(monkeypatch):
    from apps.api.app import main, promotion_surface, risk_surface, watchdog_surface
    from apps.api.app.settings import Settings

    configured = Settings(operator_api_token="o" * 32, watchdog_api_token=WATCH)
    for module in (main, promotion_surface, risk_surface, watchdog_surface):
        monkeypatch.setattr(module, "settings", configured)
    resets = (
        promotion_surface.reset_promotion_state,
        risk_surface.reset_risk_state,
        watchdog_surface.reset_watchdog_state,
    )
    for reset in resets:
        reset()
    yield watchdog_surface
    for reset in resets:
        reset()


def test_the_watchdog_token_can_only_beat_and_halt(api_app) -> None:
    from apps.api.app import main

    beat = {"watchdog_id": "wd-1", "trips": [], "checks": {"api_reachable": "ok"}}
    with TestClient(main.app) as client:
        assert client.post("/v1/watchdog/heartbeat", json=beat).status_code == 401
        operator = {"X-Proto-Operator-Token": "o" * 32}
        assert client.post("/v1/watchdog/heartbeat", json=beat, headers=operator).status_code == 401
        token = {API_TOKEN_HEADER: WATCH}
        assert client.post("/v1/watchdog/heartbeat", json=beat, headers=token).status_code == 200
        assert client.get("/v1/watchdog/status").json()["active"] is True

        halted = client.post(
            "/v1/watchdog/halt",
            json={"watchdog_id": "wd-1", "reasons": ["daily_loss:USDT"]},
            headers=token,
        )
        assert halted.status_code == 200 and halted.json()["controls"]["global_kill"] is True
        again = client.post(
            "/v1/watchdog/halt",
            json={"watchdog_id": "wd-1", "reasons": ["daily_loss:USDT"]},
            headers=token,
        )
        assert again.json()["already_halted"] is True
        # The watchdog token cannot release, nor reach operator routes.
        release = {"scope": "GLOBAL", "action": "RELEASE"}
        assert client.post("/v1/risk/killswitch", json=release, headers=token).status_code == 401
        assert client.post("/v1/watchdog/release", json={}, headers=token).status_code in (404, 405)
        state = client.get("/v1/floor/state").json()
    assert state["controls"]["global_kill"] is True
    assert any(a["headline"].startswith("watchdog halt: daily_loss") for a in state["actions"])


def test_watchdog_routes_fail_closed_without_a_configured_token(monkeypatch) -> None:
    from apps.api.app import main, watchdog_surface
    from apps.api.app.settings import Settings

    monkeypatch.setattr(watchdog_surface, "settings", Settings())
    with TestClient(main.app) as client:
        reply = client.post(
            "/v1/watchdog/halt",
            json={"watchdog_id": "x", "reasons": ["r"]},
            headers={API_TOKEN_HEADER: WATCH},
        )
    assert reply.status_code == 503


def test_the_watchdog_token_must_be_its_own() -> None:
    from apps.api.app.settings import Settings

    with pytest.raises(ValidationError, match="must differ"):
        Settings(operator_api_token="o" * 32, watchdog_api_token="o" * 32)


def test_coverage_breaks_on_a_gap_and_resets_for_another_watchdog() -> None:
    from apps.api.app.watchdog_surface import MAX_GAP, WatchdogState

    state = WatchdogState()
    for step in range(120):
        state.beat("wd-1", {}, NOW + timedelta(seconds=step * 10))
    assert state.coverage(NOW + timedelta(seconds=1190)) == timedelta(seconds=1190)
    later = NOW + timedelta(seconds=1190) + MAX_GAP + timedelta(seconds=1)
    state.beat("wd-1", {}, later)
    assert state.coverage(later) == timedelta(0)
    state.beat("wd-2", {}, later + timedelta(seconds=5))
    assert state.covered_since == later + timedelta(seconds=5)
    assert not state.active(later + timedelta(minutes=5))


def test_watchdog_evidence_passes_only_after_a_full_day_of_coverage(api_app, monkeypatch) -> None:
    from apps.api.app import main

    clock = {"now": NOW}
    monkeypatch.setattr(api_app, "_utc_now", lambda: clock["now"])
    scope = {
        "account": "paper-binance-spot",
        "venue": "BINANCE_SPOT",
        "strategy_id": "hybrid-fusion-v1",
        "model_version": "fusion-v1",
        "credential_id": "paper",
    }
    operator = {"X-Proto-Operator-Token": "o" * 32}

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    from apps.api.app import promotion_surface

    monkeypatch.setattr(promotion_surface, "datetime", Frozen)
    with TestClient(main.app) as client:
        token = {API_TOKEN_HEADER: WATCH}
        beat = {"watchdog_id": "wd-1"}
        client.post("/v1/watchdog/heartbeat", json=beat, headers=token)
        early = client.post(
            "/v1/promotion/evidence/watchdog", json={"scope": scope}, headers=operator
        ).json()
        assert early["passed"] is False and "covered=0.0h" in early["reference"]
        for _ in range(24 * 6):  # every 10 minutes is a gap: coverage restarts
            clock["now"] += timedelta(minutes=10)
            client.post("/v1/watchdog/heartbeat", json=beat, headers=token)
        assert (
            client.post(
                "/v1/promotion/evidence/watchdog", json={"scope": scope}, headers=operator
            ).json()["passed"]
            is False
        )
        for _ in range(int(24 * 3600 / 20) + 1):
            clock["now"] += timedelta(seconds=20)
            client.post("/v1/watchdog/heartbeat", json=beat, headers=token)
        passed = client.post(
            "/v1/promotion/evidence/watchdog", json={"scope": scope}, headers=operator
        )
        assert (
            client.post("/v1/promotion/evidence/watchdog", json={"scope": scope}).status_code == 401
        )
    assert passed.json()["passed"] is True
    evidence = passed.json()["scope"]["evidence"][-1]
    assert evidence["kind"] == "WATCHDOG_ACTIVE" and evidence["passed"] is True


# --- executor -----------------------------------------------------------------------


def test_executor_watchdog_token_halts_but_cannot_trade_or_release(tmp_path) -> None:
    from test_executor import body, factory, intent, settings

    from apps.executor.main import create_app
    from apps.executor.runtime import Executor

    fake = FakeBinance()
    fake.server_time_ms = int(datetime.now(UTC).timestamp() * 1000)
    fake.time_tolerance_ms = 10_000
    instance = Executor(
        settings(watchdog_token=EXEC_WATCH),
        adapter_factory=factory(fake),
        engine=create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ex.db'}"),
    )
    api_auth = {"Authorization": f"Bearer {'t' * 32}"}
    watch = {"Authorization": f"Bearer {EXEC_WATCH}"}
    with TestClient(create_app(instance)) as client:
        status = client.get("/v1/watchdog/status", headers=watch).json()
        assert (
            status["ready"] is True and status["consistent"] is True and status["last_reconcile_at"]
        )
        assert client.get("/v1/watchdog/status", headers=api_auth).status_code == 401
        assert client.post("/v1/orders", json=body(intent()), headers=watch).status_code == 401
        assert client.post("/v1/halt/release", headers=watch).status_code == 401

        halted = client.post(
            "/v1/watchdog/halt",
            json={"watchdog_id": "wd-1", "reasons": ["daily_loss:USDT"]},
            headers=watch,
        )
        assert halted.json()["halted"] == "watchdog: daily_loss:USDT"
        assert client.post("/v1/orders", json=body(intent()), headers=api_auth).status_code == 409
        assert client.get("/health").json()["status"] == "halted"
        assert (
            client.post(
                "/v1/watchdog/halt",
                json={"watchdog_id": "wd-1", "reasons": ["again"]},
                headers=watch,
            ).json()["already_halted"]
            is True
        )
        events = client.get("/v1/events", headers=api_auth).json()["events"]
    assert [e["event_type"] for e in events].count("WATCHDOG_HALT") == 1


def test_executor_refuses_a_shared_watchdog_token() -> None:
    from test_executor import settings

    with pytest.raises(ValidationError, match="must differ"):
        settings(watchdog_token="t" * 32)


# --- independence ---------------------------------------------------------------------


def test_the_watchdog_depends_on_neither_the_api_nor_the_executor() -> None:
    forbidden = (
        "apps.api",
        "apps.executor",
        "services.venues",
        "services.oms",
        "services.risk",
        "services.agents",
    )
    for root in ("apps/watchdog", "services/watchdog"):
        for path in Path(root).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    modules = [node.module]
                for module in modules:
                    assert not module.startswith(forbidden), f"{path} imports {module}"


def test_a_deploy_restart_is_not_an_outage_but_a_real_one_still_trips() -> None:
    from apps.watchdog.settings import WatchdogSettings

    token = "w" * 40
    settings = WatchdogSettings(api_url="http://api", api_token=token)
    assert settings.outage_grace_seconds == 180 and settings.unreachable_cycles == 36
    checks = Checks(limits=Limits(unreachable_cycles=settings.unreachable_cycles))
    down = obs(api_health=False, floor=None, market_ready=None)
    restart = [checks.evaluate(down)[0] for _ in range(35)]  # ~3 min of a restarting API
    assert restart == [[]] * 35
    assert checks.evaluate(down)[0] == ["api_unreachable"]
    with pytest.raises(ValidationError):  # the grace is bounded: 5 min at most
        WatchdogSettings(api_url="http://api", api_token=token, outage_grace_seconds=600)
