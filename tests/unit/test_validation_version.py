from services.validation.version import VALIDATION_SCHEMA_VERSION


def test_validation_schema_version_is_explicit() -> None:
    assert VALIDATION_SCHEMA_VERSION == "1.0.0"
