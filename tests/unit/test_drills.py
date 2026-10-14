"""Phase 6b drills: pass on the real controls, reproduce, catch broken ones, feed the gates."""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from services.drills import DRILL_VERSION, DRILLS, adversarial, recovery, rollback, run_drill
from services.drills.__main__ import main as cli
from services.execution import ReleaseStage
from services.promotion import EvidenceKind, PromotionRegistry, Scope
from services.risk import RiskDecision, RiskEngine

START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def quiet():
    logging.disable(logging.WARNING)
    yield
    logging.disable(logging.NOTSET)


@pytest.mark.parametrize("kind", DRILLS)
async def test_every_drill_passes_on_the_real_controls_and_reproduces(kind) -> None:
    first, second = await run_drill(kind, 7), await run_drill(kind, 7)
    assert first.passed, [c for c in first.failures]
    assert first.digest == second.digest
    assert (
        first.reference == f"drill:{kind.value}:v{DRILL_VERSION}:seed=7:sha256={first.digest[:16]}"
    )


async def test_the_adversarial_seed_changes_the_fuzz_and_the_digest() -> None:
    assert adversarial.run(1).digest != adversarial.run(2).digest
    assert (await recovery.run(1)).passed and (await rollback.run(2)).passed


class ApproveEverything(RiskEngine):
    """A broken engine: every intent gets a permit."""

    def evaluate(
        self, intent, *, account, controls, quote, now, scope_stage=None, canary_capital=None
    ) -> RiskDecision:
        return RiskDecision(True, self._signer.issue(intent, now=now), (), {}, None)


def test_the_adversarial_suite_catches_an_engine_that_approves_everything() -> None:
    from services.execution.catalog import PAPER_INSTRUMENTS

    report = adversarial.run(
        7, engine=ApproveEverything(instruments=PAPER_INSTRUMENTS, signer=adversarial.SIGNER)
    )
    assert not report.passed
    failed = {c.name for c in report.failures}
    assert {
        "oversized_position",
        "global_kill_switch",
        "uncertified_canary",
        "fuzz_constitution_violations",
    } <= failed


async def test_the_recovery_drill_catches_an_oms_that_resends(monkeypatch) -> None:
    from services.oms import OrderManager

    class Resending(OrderManager):
        async def resolve_unknown(self, client_order_id):
            order = self.get(client_order_id)
            with contextlib.suppress(Exception):
                await self._adapter.submit(  # noqa: SLF001 - the bug under test
                    recovery.VenueOrder(
                        client_order_id=client_order_id,
                        instrument_id=order.intent.instrument_id,
                        side=order.intent.side,
                        quantity=order.intent.quantity,
                        order_type=order.intent.order_type,
                        limit_price=order.intent.limit_price,
                        time_in_force=order.intent.time_in_force,
                    )
                )
            return await super().resolve_unknown(client_order_id)

    monkeypatch.setattr(recovery, "OrderManager", Resending)
    report = await recovery.run(0)
    assert "lost_response_sent_once" in {c.name for c in report.failures}


async def test_the_rollback_drill_catches_a_registry_that_does_not_demote(monkeypatch) -> None:
    async def ignore(self, *, venue, kind, detail):
        return []

    monkeypatch.setattr(PromotionRegistry, "violation", ignore)
    report = await rollback.run(0)
    assert {"violation_demotes_canary_to_paper", "canary_intent_refused_after_demotion"} <= {
        c.name for c in report.failures
    }


async def test_a_control_that_raises_fails_the_drill_instead_of_aborting_it(monkeypatch) -> None:
    from services.risk import RiskGovernor

    def explode(self, scope, key=None):
        raise RuntimeError("kill switch unavailable")

    monkeypatch.setattr(RiskGovernor, "trigger_kill", explode)
    report = await rollback.run(0)
    assert not report.passed
    assert report.failures[-1].name == "halts_completed"
    assert report.failures[-1].observed == "raised RuntimeError"


def test_cli_runs_and_verifies_a_report(tmp_path, capsys) -> None:
    out = tmp_path / "rollback.json"
    assert cli(["run", "ROLLBACK_DRILL", "--seed", "3", "--out", str(out)]) == 0
    saved = json.loads(out.read_text())
    assert saved["passed"] is True and saved["seed"] == 3
    assert cli(["verify", str(out)]) == 0
    assert "reproduced" in capsys.readouterr().out

    out.write_text(json.dumps({**saved, "digest": "0" * 64}))
    assert cli(["verify", str(out)]) == 1
    out.write_text(json.dumps({**saved, "version": "0"}))
    assert cli(["verify", str(out)]) == 2


# --- the gates, end to end through the API -------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def api(monkeypatch):
    from apps.api.app import main, promotion_surface, risk_surface, watchdog_surface
    from apps.api.app.settings import Settings

    configured = Settings(operator_api_token="o" * 32, watchdog_api_token="w" * 32)
    for module in (main, promotion_surface, risk_surface, watchdog_surface):
        monkeypatch.setattr(module, "settings", configured)
    clock = Clock()
    registry = PromotionRegistry(clock=clock)
    monkeypatch.setattr(promotion_surface, "registry", lambda: registry)
    monkeypatch.setattr(watchdog_surface, "_utc_now", clock)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now

    monkeypatch.setattr(promotion_surface, "datetime", Frozen)
    watchdog_surface.reset_watchdog_state()
    risk_surface.reset_risk_state()
    yield clock, registry
    watchdog_surface.reset_watchdog_state()
    risk_surface.reset_risk_state()


def test_phase_6b_evidence_unlocks_the_canary_gate(api) -> None:
    from apps.api.app import main

    clock, registry = api
    operator = {"X-Proto-Operator-Token": "o" * 32}
    watch = {"X-Proto-Watchdog-Token": "w" * 32}
    scope = {
        "account": "executor-binance-spot",
        "venue": "BINANCE_SPOT",
        "strategy_id": "hybrid-fusion-v1",
        "model_version": "fusion-v1",
        "credential_id": "testnet",
    }
    with TestClient(main.app) as client:
        for stage, kind in (
            ("REPLAY", None),
            ("SHADOW", "REPLAY_REPRODUCIBLE"),
            ("PAPER", "SHADOW_COMPARISON"),
        ):
            if kind:
                client.post(
                    "/v1/promotion/evidence",
                    headers=operator,
                    json={"scope": scope, "kind": kind, "reference": "ci#1", "passed": True},
                )
            assert (
                client.post(
                    "/v1/promotion/promote",
                    headers=operator,
                    json={"scope": scope, "target": stage},
                ).status_code
                == 200
            )

        # A week in PAPER, watched the whole last day.
        clock.now += timedelta(days=6)
        for _ in range(int(24 * 3600 / 20) + 2):
            clock.now += timedelta(seconds=20)
            client.post("/v1/watchdog/heartbeat", headers=watch, json={"watchdog_id": "wd-1"})

        assert (
            client.post(
                "/v1/promotion/evidence/drill", json={"scope": scope, "kind": "RECOVERY_DRILL"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/v1/promotion/evidence/drill",
                headers=operator,
                json={"scope": scope, "kind": "SHADOW_COMPARISON"},
            ).status_code
            == 422
        )
        for kind in ("ADVERSARIAL_SUITE", "RECOVERY_DRILL", "ROLLBACK_DRILL"):
            reply = client.post(
                "/v1/promotion/evidence/drill",
                headers=operator,
                json={"scope": scope, "kind": kind, "seed": 11},
            ).json()
            assert reply["drill"]["passed"] is True and reply["drill"]["failures"] == []
            assert reply["drill"]["reference"].startswith(
                f"drill:{kind}:v{DRILL_VERSION}:seed=11:sha256="
            )
        watched = client.post(
            "/v1/promotion/evidence/watchdog", headers=operator, json={"scope": scope}
        ).json()
        assert watched["passed"] is True

        canary = client.post(
            "/v1/promotion/promote", headers=operator, json={"scope": scope, "target": "CANARY"}
        )
    assert canary.status_code == 200, canary.json()
    assert canary.json()["scope"]["stage"] == ReleaseStage.CANARY.value
    evidence = {e["kind"]: e for e in canary.json()["scope"]["evidence"]}
    assert all(evidence[k.value]["passed"] for k in (*DRILLS, EvidenceKind.WATCHDOG_ACTIVE))


async def test_a_failed_drill_records_failed_evidence_and_keeps_the_gate_shut(
    api, monkeypatch
) -> None:
    from apps.api.app import main
    from services import drills

    clock, registry = api
    real = drills.run_drill

    async def broken(kind, seed=0):
        report = await real(kind, seed)
        report.expect("injected_failure", "ok", "broken")
        return report

    monkeypatch.setattr(drills, "run_drill", broken)
    scope = {
        "account": "a",
        "venue": "BINANCE_SPOT",
        "strategy_id": "s",
        "model_version": "m",
        "credential_id": "c",
    }
    with TestClient(main.app) as client:
        reply = client.post(
            "/v1/promotion/evidence/drill",
            headers={"X-Proto-Operator-Token": "o" * 32},
            json={"scope": scope, "kind": "ROLLBACK_DRILL"},
        ).json()
    assert reply["drill"]["passed"] is False and reply["drill"]["failures"] == ["injected_failure"]
    record = await registry.get(Scope("a", "BINANCE_SPOT", "s", "m", "c"))
    assert record.evidence[-1].passed is False
