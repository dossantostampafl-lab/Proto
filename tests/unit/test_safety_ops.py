"""Phase 7 operations: safety metrics and alerts, watchdog deployment, executor halt relay."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from services.execution import ReleaseStage
from services.promotion import PromotionRegistry, Scope

WATCH = {"X-Proto-Watchdog-Token": "w" * 32}
OPERATOR = {"X-Proto-Operator-Token": "o" * 32}


@pytest.fixture
def api(monkeypatch):
    from apps.api.app import main, oms_surface, risk_surface, watchdog_surface
    from apps.api.app.settings import Settings

    configured = Settings(operator_api_token="o" * 32, watchdog_api_token="w" * 32)
    for module in (main, oms_surface, risk_surface, watchdog_surface):
        monkeypatch.setattr(module, "settings", configured)
    resets = (
        risk_surface.reset_risk_state,
        watchdog_surface.reset_watchdog_state,
        oms_surface.reset_oms_state,
    )
    for reset in resets:
        reset()
    yield oms_surface
    for reset in resets:
        reset()


def metrics(client: TestClient) -> dict[str, float]:
    text = client.get("/v1/safety/metrics/prometheus").text
    return {
        line.split()[0]: float(line.split()[1])
        for line in text.splitlines()
        if line and not line.startswith("#")
    }


SCOPE = Scope("acct", "BINANCE_SPOT", "hybrid-fusion-v1", "fusion-v1", "testnet")
LABELS = (
    'account="acct",venue="BINANCE_SPOT",strategy="hybrid-fusion-v1",'
    'model="fusion-v1",credential="testnet"'
)


@pytest.fixture
def scoped(api, monkeypatch):
    """A registry with one scope in REPLAY, so the promotion series have a row."""

    from apps.api.app import promotion_surface

    registry = PromotionRegistry()
    monkeypatch.setattr(promotion_surface, "registry", lambda: registry)
    asyncio.run(registry.promote(SCOPE, ReleaseStage.REPLAY, operator=False, by="test"))
    return registry


def exported_names(client: TestClient) -> set[str]:
    return {name.split("{")[0] for name in metrics(client)}


def test_safety_metrics_follow_the_watchdog_and_the_halts(api) -> None:
    from apps.api.app import main

    with TestClient(main.app) as client:
        before = metrics(client)
        assert before["proto_watchdog_active"] == 0 and before["proto_global_kill_switch"] == 0
        assert before["proto_real_money_execution"] == 0 and before["proto_journal_valid"] == 1

        client.post("/v1/watchdog/heartbeat", headers=WATCH, json={"watchdog_id": "wd-1"})
        client.post(
            "/v1/watchdog/halt",
            headers=WATCH,
            json={"watchdog_id": "wd-1", "reasons": ["daily_loss:USDT"]},
        )
        after = metrics(client)
        assert after["proto_watchdog_active"] == 1
        assert after["proto_watchdog_trips_total"] == 1 and after["proto_global_kill_switch"] == 1

        client.post(
            "/v1/risk/killswitch", headers=OPERATOR, json={"scope": "GLOBAL", "action": "RELEASE"}
        )
        assert metrics(client)["proto_global_kill_switch"] == 0


def test_promotion_metrics_follow_the_scope_stage(scoped) -> None:
    from apps.api.app import main

    with TestClient(main.app) as client:
        replay = metrics(client)
        assert replay[f"proto_promotion_stage{{{LABELS}}}"] == 1  # REPLAY
        assert replay[f"proto_promotion_violations{{{LABELS}}}"] == 0
        assert replay[f"proto_promotion_blockers{{{LABELS}}}"] >= 1  # no replay evidence yet
        asyncio.run(scoped.demote(SCOPE, reason="test", by="operator"))
        assert metrics(client)[f"proto_promotion_stage{{{LABELS}}}"] == 0  # RESEARCH


def test_every_alert_reads_a_metric_that_is_exported(scoped) -> None:
    from apps.api.app import main

    with TestClient(main.app) as client:
        exported = exported_names(client)
    rules = yaml.safe_load(Path("infra/monitoring/safety-alerts.yml").read_text())["groups"][0][
        "rules"
    ]
    for rule in rules:
        names = set(re.findall(r"\bproto_[a-z_]+", rule["expr"]))
        assert names <= exported, f"{rule['alert']} reads {names - exported}"
    assert {r["alert"] for r in rules} >= {
        "ProtoWatchdogDown",
        "ProtoWatchdogTripped",
        "ProtoLockdownLatched",
        "ProtoJournalInvalid",
        "ProtoScopeDemoted",
    }


def test_the_safety_dashboard_reads_only_exported_metrics(scoped) -> None:
    from apps.api.app import main

    with TestClient(main.app) as client:
        exported = exported_names(client)
    dashboard = json.loads(
        Path("infra/monitoring/grafana/dashboards-trading/proto-safety.json").read_text()
    )
    exprs = [t["expr"] for p in dashboard["panels"] for t in p["targets"]]
    used = {n for e in exprs for n in re.findall(r"\bproto_[a-z_]+", e)}
    assert used and used <= exported, used - exported
    assert {"proto_real_money_execution", "proto_promotion_stage"} <= used
    assert dashboard["editable"] is False  # read-only: no buttons, no writes

    compose = yaml.safe_load(Path("docker-compose.yml").read_text())["services"]["grafana"]
    assert (
        "./infra/monitoring/grafana/provisioning-trading:/etc/grafana/provisioning:ro"
        in compose["volumes"]
    )
    providers = yaml.safe_load(
        Path("infra/monitoring/grafana/provisioning-trading/dashboards/default.yml").read_text()
    )["providers"]
    paths = {p["options"]["path"] for p in providers}
    assert paths == {"/var/lib/grafana/dashboards", "/var/lib/grafana/dashboards-trading"}
    mounts = {v.split(":")[1] for v in compose["volumes"]}
    assert paths <= mounts
    # The read-only live stack keeps its own provisioning and never sees the trading dashboard.
    live = yaml.safe_load(Path("docker-compose.live.yml").read_text())["services"]["grafana"]
    assert not any("trading" in v for v in live["volumes"])


def test_prometheus_and_compose_wire_the_safety_job_and_the_watchdog() -> None:
    prometheus = yaml.safe_load(Path("infra/monitoring/prometheus-trading.yml").read_text())
    assert "/etc/prometheus/safety-alerts.yml" in prometheus["rule_files"]
    jobs = {j["job_name"]: j for j in prometheus["scrape_configs"]}
    assert jobs["proto-safety"]["metrics_path"] == "/v1/safety/metrics/prometheus"

    compose = yaml.safe_load(Path("docker-compose.yml").read_text())["services"]
    volumes = compose["prometheus"]["volumes"]
    assert any("safety-alerts.yml" in v for v in volumes)
    assert "./infra/monitoring/prometheus-trading.yml:/etc/prometheus/prometheus.yml:ro" in volumes
    # The read-only deployment has no trading surfaces, so it keeps its own config untouched.
    live = yaml.safe_load(Path("infra/monitoring/prometheus.yml").read_text())
    assert "proto-safety" not in {j["job_name"] for j in live["scrape_configs"]}
    watchdog = compose["watchdog"]
    assert watchdog["profiles"] == ["watchdog"]  # opt in: never started without its token
    assert watchdog["command"] == ["python", "-m", "apps.watchdog"]
    assert watchdog["environment"]["WATCHDOG_API_URL"] == "http://api:8000"
    assert "WATCHDOG_API_TOKEN" in compose["api"]["environment"]
    # The watchdog never gets the operator token or any credential.
    assert not any(
        k.startswith(("OPERATOR_", "PROTO_CRED_", "EXECUTOR_API")) for k in watchdog["environment"]
    )

    unit = Path("infra/watchdog/proto-watchdog.service").read_text()
    assert "ExecStart=/opt/proto/.venv/bin/python -m apps.watchdog" in unit
    assert "Restart=always" in unit and "NoNewPrivileges=true" in unit


def test_operator_releases_the_executor_halt_through_the_api(api, monkeypatch) -> None:
    from apps.api.app import main
    from apps.api.app.event_state import operational_journal
    from apps.api.app.executor_client import ExecutorClient

    with TestClient(main.app) as client:
        assert client.post("/v1/oms/executor/halt/release").status_code == 401
        assert (
            client.post("/v1/oms/executor/halt/release", headers=OPERATOR).status_code == 409
        )  # paper

    calls: list[str] = []
    diverged = {"value": True}

    def executor(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        assert request.headers["Authorization"] == f"Bearer {'e' * 32}"
        if diverged["value"]:
            return httpx.Response(409, json={"detail": "still diverged from the venue"})
        return httpx.Response(
            200, json={"consistent": True, "divergences": [], "venue_halted": False}
        )

    relay = ExecutorClient(
        "http://executor",
        "e" * 32,
        client=httpx.AsyncClient(transport=httpx.MockTransport(executor)),
    )
    monkeypatch.setattr(api, "executor_mode", lambda: True)
    monkeypatch.setattr(api, "executor_client", lambda: relay)
    with TestClient(main.app) as client:
        refused = client.post("/v1/oms/executor/halt/release", headers=OPERATOR)
        assert refused.status_code == 409 and "still diverged" in refused.json()["detail"]
        diverged["value"] = False
        released = client.post("/v1/oms/executor/halt/release", headers=OPERATOR)
    assert released.status_code == 200 and released.json()["venue_halted"] is False
    assert calls == ["POST /v1/halt/release", "POST /v1/halt/release"]
    assert any(
        e.event_type == "EXECUTOR_HALT_RELEASED" for e in operational_journal.journal.recent(20)
    )
