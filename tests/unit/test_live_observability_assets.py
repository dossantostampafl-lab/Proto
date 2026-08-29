import json
import re
from pathlib import Path

_PROMETHEUS = Path("infra/monitoring/prometheus.yml")
_ALERTS = Path("infra/monitoring/live-alerts.yml")
_DASHBOARD = Path("infra/monitoring/grafana/dashboards/live-monitoring.json")
_LIVE_METRICS = Path("apps/api/app/live_metrics.py")
_DOCKER_COMPOSE = Path("docker-compose.yml")
_LIVE_COMPOSE = Path("docker-compose.live.yml")

_METRIC_PATTERN = re.compile(r"\bproto_live_[a-z0-9_]+\b")


def _defined_live_metrics() -> set[str]:
    return set(_METRIC_PATTERN.findall(_LIVE_METRICS.read_text(encoding="utf-8")))


def test_prometheus_scrapes_only_standalone_live_metrics_and_loads_alerts() -> None:
    config = _PROMETHEUS.read_text(encoding="utf-8")

    assert "job_name: proto-live-read-only" in config
    assert "metrics_path: /live/metrics/prometheus" in config
    assert "/etc/prometheus/live-alerts.yml" in config
    assert "metrics_path: /metrics/prometheus" not in config
    assert "job_name: proto-api" not in config


def test_alert_rules_reference_only_exported_live_metrics_and_safety_invariants() -> None:
    alerts = _ALERTS.read_text(encoding="utf-8")
    referenced = set(_METRIC_PATTERN.findall(alerts))

    assert referenced <= _defined_live_metrics()
    assert "ProtoLivePersistenceUnavailable" in alerts
    assert "ProtoLiveSymbolReceiptStale" in alerts
    assert "ProtoLiveFinancialConnectivityInvariantBroken" in alerts
    assert "ProtoLiveRealMoneyExecutionInvariantBroken" in alerts
    assert "proto_live_financial_connectivity != 0" in alerts
    assert "proto_live_real_money_execution != 0" in alerts


def test_grafana_dashboard_is_valid_json_and_uses_only_live_metrics() -> None:
    raw = _DASHBOARD.read_text(encoding="utf-8")
    dashboard = json.loads(raw)
    referenced = set(_METRIC_PATTERN.findall(raw))

    assert dashboard["uid"] == "proto-live-read-only"
    assert dashboard["title"] == "Proto Live Monitoring"
    assert referenced <= _defined_live_metrics()
    assert "proto_live_financial_connectivity" in referenced
    assert "proto_live_real_money_execution" in referenced


def test_compose_mounts_alerts_dashboard_and_persistent_prometheus_storage() -> None:
    compose = _DOCKER_COMPOSE.read_text(encoding="utf-8")

    assert "./infra/monitoring/live-alerts.yml:/etc/prometheus/live-alerts.yml:ro" in compose
    assert "proto_prometheus:/prometheus" in compose
    assert "./infra/monitoring/grafana/provisioning:/etc/grafana/provisioning:ro" in compose
    assert "./infra/monitoring/grafana/dashboards:/var/lib/grafana/dashboards:ro" in compose


def test_live_only_staging_compose_has_no_legacy_web_and_binds_admin_ports_locally() -> None:
    compose = _LIVE_COMPOSE.read_text(encoding="utf-8")

    assert "apps.api.app.live_app:app" not in compose  # image entrypoint owns the app target
    assert "\n  web:\n" not in compose
    assert '"127.0.0.1:8000:8000"' in compose
    assert '"127.0.0.1:9090:9090"' in compose
    assert '"127.0.0.1:3000:3000"' in compose
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose
    assert "PROTO_DB_PASSWORD:?set PROTO_DB_PASSWORD" in compose
    assert "GRAFANA_ADMIN_PASSWORD:?set GRAFANA_ADMIN_PASSWORD" in compose
