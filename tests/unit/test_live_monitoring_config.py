from pathlib import Path


def test_prometheus_scrapes_live_read_only_metrics_without_replacing_api_metrics() -> None:
    config = Path("infra/monitoring/prometheus.yml").read_text(encoding="utf-8")

    assert "job_name: proto-api" in config
    assert "metrics_path: /metrics/prometheus" in config
    assert "job_name: proto-live-read-only" in config
    assert "metrics_path: /live/metrics/prometheus" in config
    assert config.count("- api:8000") == 2
