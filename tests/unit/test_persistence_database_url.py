from apps.api.app.persistence import build_engine


def test_build_engine_normalizes_plain_postgresql_url_to_asyncpg() -> None:
    engine = build_engine("postgresql://user:pass@db.internal:5432/proto")

    assert engine.url.drivername == "postgresql+asyncpg"
