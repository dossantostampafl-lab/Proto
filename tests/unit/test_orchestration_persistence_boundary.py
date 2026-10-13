from __future__ import annotations

from pathlib import Path

from apps.api.app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
_TEST_OPERATOR_TOKEN = "o" * 32


def test_api_enables_orchestration_without_general_simulation_persistence() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "APP_ENV=production" in dockerfile
    assert "ORCHESTRATION_PERSISTENCE_ENABLED=true" in dockerfile
    assert "\n    PERSISTENCE_ENABLED=true" not in dockerfile


def test_effective_orchestration_persistence_honors_explicit_switch() -> None:
    settings = Settings(
        orchestration_persistence_enabled=True,
        live_persistence_enabled=False,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    assert settings.orchestration_persistence_active is True
    assert settings.persistence_enabled is False


def test_production_durable_postgres_activates_orchestration_without_simulation() -> None:
    settings = Settings(
        app_env="production",
        operator_api_token=_TEST_OPERATOR_TOKEN,
        orchestration_persistence_enabled=False,
        live_persistence_enabled=False,
        database_url="postgresql+asyncpg://proto:proto@db/proto",
    )
    assert settings.database_driver == "postgresql+asyncpg"
    assert settings.durable_database_configured is True
    assert settings.orchestration_persistence_active is True
    assert settings.persistence_enabled is False


def test_effective_orchestration_persistence_reuses_durable_live_postgres() -> None:
    settings = Settings(
        orchestration_persistence_enabled=False,
        live_persistence_enabled=True,
        database_url="postgresql+asyncpg://proto:proto@db/proto",
    )
    assert settings.orchestration_persistence_active is True
    assert settings.persistence_enabled is False


def test_effective_orchestration_persistence_never_derives_from_ephemeral_sqlite() -> None:
    settings = Settings(
        app_env="production",
        operator_api_token=_TEST_OPERATOR_TOKEN,
        orchestration_persistence_enabled=False,
        live_persistence_enabled=True,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    assert settings.durable_database_configured is False
    assert settings.orchestration_persistence_active is False


def test_orchestration_engine_isolated_from_simulation_persistence() -> None:
    app_state = (ROOT / "apps/api/app/app_state.py").read_text()
    assert "settings.orchestration_persistence_active" in app_state
    assert "orchestration_engine" in app_state
    assert "AsyncSqlFillJournal(persistence_engine)" in app_state
    assert "SqlJobStore(orchestration_engine)" in app_state
    assert "DecisionMemoryStore(orchestration_engine)" in app_state


def test_orchestration_status_exposes_only_sanitized_database_diagnostics() -> None:
    from fastapi.testclient import TestClient

    from apps.api.app.main import app

    body = TestClient(app).get("/orchestration/status").text

    assert "database_driver" in body
    assert "database_url" not in body
    assert "://" not in body  # no DSN (and therefore no credentials) leaks
