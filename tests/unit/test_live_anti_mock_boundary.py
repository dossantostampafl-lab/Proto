from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE_MODULES = (
    "apps/api/app/live_app.py",
    "apps/api/app/live_routes.py",
    "apps/api/app/live_monitor.py",
    "apps/api/app/live_payloads.py",
    "apps/api/app/live_persistence.py",
    "apps/api/app/live_persistence_coordinator.py",
)
FORBIDDEN_LIVE_TOKENS = (
    "SyntheticAdapter",
    "MockPredictionMarketAdapter",
    "SYNTHETIC_DEMO",
    "SYNTHETIC_RESEARCH_BASELINE",
)


def test_live_runtime_does_not_import_simulation_or_synthetic_sources() -> None:
    violations: list[str] = []
    for relative_path in LIVE_MODULES:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        for token in FORBIDDEN_LIVE_TOKENS:
            if token in source:
                violations.append(f"{relative_path}: {token}")

    assert violations == [], "live runtime crossed synthetic boundary: " + ", ".join(violations)


# Dashboard provenance (live / research / paper tags, never "live edge") is enforced by
# apps/web/scripts/contract-check.mjs in the web CI job.
