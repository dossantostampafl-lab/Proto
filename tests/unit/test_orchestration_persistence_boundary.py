from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_railway_enables_orchestration_without_general_simulation_persistence() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "APP_ENV=production" in dockerfile
    assert "ORCHESTRATION_PERSISTENCE_ENABLED=true" in dockerfile
    assert "\n    PERSISTENCE_ENABLED=true" not in dockerfile


def test_settings_expose_separate_orchestration_persistence_switch() -> None:
    settings = (ROOT / "apps/api/app/settings.py").read_text()
    assert "orchestration_persistence_enabled: bool = False" in settings


def test_orchestration_engine_isolated_from_simulation_persistence() -> None:
    app_state = (ROOT / "apps/api/app/app_state.py").read_text()
    assert "settings.orchestration_persistence_enabled" in app_state
    assert "orchestration_engine" in app_state
    assert "AsyncSqlFillJournal(persistence_engine)" in app_state
    assert "SqlJobStore(orchestration_engine)" in app_state
    assert "DecisionMemoryStore(orchestration_engine)" in app_state


def test_production_surface_does_not_claim_durability_for_ephemeral_sqlite() -> None:
    surface = (ROOT / "apps/api/app/orchestration_surface.py").read_text()
    assert 'startswith("postgresql")' in surface
    assert '"durable_backend": durable_backend' in surface
    assert '"durable_safe_scope": safe_scope_ready and durable_backend' in surface
    assert '"general_simulation_persistence_enabled": settings.persistence_enabled' in surface
