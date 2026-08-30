import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.live.yml"
DOCKERFILE = ROOT / "Dockerfile.api"
DOCKERIGNORE = ROOT / ".dockerignore"
LIVE_REQUIREMENTS = ROOT / "requirements-live.txt"
PROMETHEUS = ROOT / "infra/monitoring/prometheus.yml"
ALERTS = ROOT / "infra/monitoring/live-alerts.yml"
DASHBOARD = ROOT / "infra/monitoring/grafana/dashboards/live-monitoring.json"


def test_live_staging_is_migration_first_and_fail_closed() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "migrate:" in compose
    assert 'command: ["alembic", "upgrade", "head"]' in compose
    assert "condition: service_completed_successfully" in compose
    assert 'LIVE_DATABASE_AUTO_CREATE: "false"' in compose
    assert 'PERSISTENCE_ENABLED: "true"' in compose
    assert 'SYSTEM_MODE: LIVE_MONITORING' in compose
    assert 'LIVE_MONITORING_AUTOSTART: "true"' in compose
    assert "${PROTO_DB_PASSWORD:?set PROTO_DB_PASSWORD}" in compose


def test_live_staging_services_are_hardened_and_bound_to_loopback() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert compose.count("read_only: true") >= 2
    assert compose.count("no-new-privileges:true") >= 2
    assert compose.count("- ALL") >= 2
    assert '"127.0.0.1:8000:8000"' in compose
    assert '"127.0.0.1:9090:9090"' in compose
    assert '"127.0.0.1:3000:3000"' in compose
    assert "0.0.0.0:" not in compose

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "USER proto:proto" in dockerfile
    assert "requirements-live.txt" in dockerfile
    assert "pip install --no-cache-dir -r requirements-live.txt" in dockerfile
    assert "apps.api.app.live_app:app" in dockerfile
    assert "apps.api.app.main:app" not in dockerfile


def test_live_image_dependency_manifest_excludes_research_stack() -> None:
    requirements = LIVE_REQUIREMENTS.read_text(encoding="utf-8").lower().splitlines()
    package_names = {line.split("<", 1)[0].split(">", 1)[0].split("[", 1)[0] for line in requirements}

    required = {
        "alembic",
        "fastapi",
        "uvicorn",
        "pydantic",
        "pydantic-settings",
        "redis",
        "sqlalchemy",
        "asyncpg",
        "websockets",
    }
    forbidden = {
        "numpy",
        "pandas",
        "polars",
        "scipy",
        "scikit-learn",
        "statsmodels",
    }
    assert required <= package_names
    assert package_names.isdisjoint(forbidden)


def test_live_docker_context_excludes_development_and_legacy_top_level_assets() -> None:
    ignored = set(DOCKERIGNORE.read_text(encoding="utf-8").splitlines())

    assert {".git", ".github", "apps/web", "docs", "engines", "tests", "scripts"} <= ignored


def test_prometheus_loads_live_rules_and_scrapes_only_live_metrics_surface() -> None:
    prometheus = PROMETHEUS.read_text(encoding="utf-8")
    alerts = ALERTS.read_text(encoding="utf-8")

    assert "/etc/prometheus/live-alerts.yml" in prometheus
    assert "job_name: proto-live-read-only" in prometheus
    assert "metrics_path: /live/metrics/prometheus" in prometheus
    assert "ProtoLivePersistenceUnavailable" in alerts
    assert "ProtoLiveHistoryBackendFailures" in alerts
    assert "ProtoLiveHistoryUnavailableRequests" in alerts
    assert "ProtoLiveFinancialConnectivityInvariantBroken" in alerts
    assert "ProtoLiveRealMoneyExecutionInvariantBroken" in alerts


def test_grafana_dashboard_contains_recovery_durability_and_history_telemetry() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    expressions = {
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
        if "expr" in target
    }

    required = {
        "proto_live_connected",
        "proto_live_all_symbols_receipts_fresh",
        "proto_live_persistence_healthy",
        "proto_live_reconnects_total",
        "proto_live_persistence_write_failures_current_connection",
        "proto_live_history_requests_total",
        "proto_live_history_backend_failures_total",
        "proto_live_financial_connectivity",
        "proto_live_real_money_execution",
    }
    assert required <= expressions
