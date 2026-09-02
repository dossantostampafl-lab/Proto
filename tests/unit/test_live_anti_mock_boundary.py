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


def test_terminal_keeps_live_research_and_paper_provenance_explicit() -> None:
    source = (ROOT / "apps/web/src/terminal.tsx").read_text(encoding="utf-8")

    required_live = (
        'requestJson<LiveStatus>("/live/status")',
        'requestJson<LiveMarketResponse>("/live/market-data")',
        'requestJson<LiveAnalytics>(`/live/analytics/${symbol}`)',
        '"LIVE PUBLIC"',
    )
    required_research = (
        'requestJson<LifecycleResponse>("/market-lifecycle")',
        'requestJson<Hawkes>(`/hawkes/${selected}`)',
        '"SYNTHETIC RESEARCH"',
        '"SYNTHETIC GREEKS"',
    )
    required_paper = (
        '"PAPER / SIM"',
        '"PAPER PORTFOLIO"',
        '"EXEC SIMULATION"',
        '"financial connectivity OFF"',
    )

    for marker in (*required_live, *required_research, *required_paper):
        assert marker in source, f"terminal provenance contract missing: {marker}"


def test_public_live_display_never_labels_probability_or_edge_as_live() -> None:
    source = (ROOT / "apps/web/src/terminal.tsx").read_text(encoding="utf-8")

    assert "MARKET P" in source
    assert "MODEL P" in source
    assert "EDGE HISTORY" in source
    assert '<Badge kind="research"/>' in source
    assert "LIVE MARKET P" not in source
    assert "LIVE MODEL P" not in source
    assert "LIVE EDGE" not in source
