import pytest
from pydantic import ValidationError

from apps.api.app.settings import Settings


def test_production_live_monitoring_rejects_general_simulation_persistence() -> None:
    with pytest.raises(ValidationError, match="PERSISTENCE_ENABLED must be false"):
        Settings(
            app_env="production",
            system_mode="LIVE_MONITORING",
            persistence_enabled=True,
            orchestration_persistence_enabled=True,
            database_url="postgresql+asyncpg://user:pass@db/proto",
            operator_api_token="o" * 32,
            creation_bridge_shared_secret="c" * 32,
        )


def test_production_live_monitoring_allows_dedicated_orchestration_persistence() -> None:
    settings = Settings(
        app_env="production",
        system_mode="LIVE_MONITORING",
        persistence_enabled=False,
        orchestration_persistence_enabled=True,
        database_url="postgresql+asyncpg://user:pass@db/proto",
        operator_api_token="o" * 32,
        creation_bridge_shared_secret="c" * 32,
    )

    assert settings.persistence_enabled is False
    assert settings.orchestration_persistence_active is True
    assert settings.durable_database_configured is True
