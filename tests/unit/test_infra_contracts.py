"""Infra contracts the finalization audit checks by hand: keep them from drifting silently.

- every metric an alert rule or a Grafana panel reads is one the API can emit;
- the Alembic chain is linear with one head;
- CI runs exactly the commands CLAUDE.md documents.
"""

import re
from pathlib import Path

import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory

MONITORING = Path("infra/monitoring")
EMITTERS = [
    Path("apps/api/app/live_metrics.py"),
    Path("apps/api/app/live_history_metrics.py"),
    Path("apps/api/app/safety_metrics.py"),
    Path("apps/api/app/research.py"),
]
METRIC = re.compile(r"\bproto_[a-z0-9_]+")


def _names(paths: list[Path]) -> set[str]:
    return {name for path in paths for name in METRIC.findall(path.read_text(encoding="utf-8"))}


def test_every_alert_and_dashboard_metric_is_emitted_by_the_api() -> None:
    consumers = [
        *MONITORING.glob("*-alerts.yml"),
        *MONITORING.glob("grafana/dashboards*/*.json"),
    ]
    assert consumers, "no alert rules or dashboards found"
    missing = _names(consumers) - _names(EMITTERS)
    assert not missing, f"alerts/dashboards read metrics nobody emits: {sorted(missing)}"


def test_each_prometheus_config_loads_only_rule_files_that_exist() -> None:
    mounted = {
        "/etc/prometheus/live-alerts.yml": MONITORING / "live-alerts.yml",
        "/etc/prometheus/safety-alerts.yml": MONITORING / "safety-alerts.yml",
    }
    for config in (MONITORING / "prometheus.yml", MONITORING / "prometheus-trading.yml"):
        spec = yaml.safe_load(config.read_text(encoding="utf-8"))
        for rule_file in spec["rule_files"]:
            assert rule_file in mounted and mounted[rule_file].exists(), (config, rule_file)
    for rule_path in mounted.values():
        groups = yaml.safe_load(rule_path.read_text(encoding="utf-8"))["groups"]
        rules = [rule for group in groups for rule in group["rules"]]
        assert rules and all(rule.get("alert") and rule.get("expr") for rule in rules), rule_path


def test_alembic_history_is_one_linear_chain() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert len(script.get_heads()) == 1
    revisions = list(script.walk_revisions())
    assert all(not isinstance(rev.down_revision, tuple) for rev in revisions), "merge revision"
    assert len({rev.revision for rev in revisions}) == len(revisions)
    files = sorted(Path("migrations/versions").glob("[0-9][0-9][0-9][0-9]_*.py"))
    assert len(files) == len(revisions), "a migration file is not on the chain"


def test_ci_runs_exactly_the_documented_gates() -> None:
    ci = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
    claude = Path("CLAUDE.md").read_text(encoding="utf-8")

    def runs(job: str) -> list[str]:
        return [step["run"] for step in ci["jobs"][job]["steps"] if "run" in step]

    python = runs("python")
    assert "pip install -e '.[dev]'" in python
    assert "ruff check apps services tests" in python
    assert "pytest" in python
    web = runs("web")
    for command in (
        "npm ci",
        "npm run typecheck",
        "npm run build",
        "npm run contract-check",
        "npm run bundle-check",
    ):
        assert command in web, command
        assert command in claude, command
    assert "ruff check apps services tests" in claude
    assert ci["jobs"]["python"]["steps"][1]["with"]["python-version"] == "3.13"
    assert ci["jobs"]["web"]["steps"][1]["with"]["node-version"] == "22"
