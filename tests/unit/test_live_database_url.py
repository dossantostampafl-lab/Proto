from apps.api.app.live_database import build_live_engine


def test_build_live_engine_normalizes_plain_postgresql_url_to_asyncpg() -> None:
    engine = build_live_engine("postgresql://user:pass@db.internal:5432/proto")

    assert engine.url.drivername == "postgresql+asyncpg"
