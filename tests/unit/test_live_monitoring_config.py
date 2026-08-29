from pathlib import Path


def test_prometheus_scrapes_only_the_standalone_live_read_only_surface() -> None:
    config = Path("infra/monitoring/prometheus.yml").read_text(encoding="utf-8")

    assert "job_name: proto-live-read-only" in config
    assert "metrics_path: /live/metrics/prometheus" in config
    assert "job_name: proto-api" not in config
    assert "metrics_path: /metrics/prometheus" not in config
    assert config.count("- api:8000") == 1
