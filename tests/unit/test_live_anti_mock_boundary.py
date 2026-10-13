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


def _web_source() -> str:
    web = ROOT / "apps/web/src"
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(web.rglob("*.ts*")))


def test_terminal_keeps_live_research_and_paper_provenance_explicit() -> None:
    source = _web_source()
    command = (ROOT / "apps/web/src/views/Command.tsx").read_text(encoding="utf-8")

    required = (
        'usePoll<LiveStatus>("/live/status")',
        '"/live/market-data"',
        "`/live/analytics/${symbol}`",
        'usePoll<{ markets: Market[] }>("/market-lifecycle"',
        "`/hawkes/${selected}`",
        '"LIVE PUBLIC"',
        '"SYNTHETIC RESEARCH"',
        '"PAPER / SIM"',
        "FINANCIAL CONNECTIVITY OFF",
        "REAL MONEY EXECUTION OFF",
    )
    for marker in required:
        assert marker in source, f"terminal provenance contract missing: {marker}"

    # Live telemetry, research and paper panels each carry their own provenance tag.
    assert 'title={`${selected}/USD`} source="live"' in command
    assert 'title="L1 microstructure" source="live"' in command
    assert 'title="Probability orbit" source="research"' in command
    assert 'title="Paper book" source="paper"' in command


def test_public_live_display_never_labels_probability_or_edge_as_live() -> None:
    source = _web_source()

    assert '"Market P"' in source
    assert '"Model P"' in source
    assert '"Net edge"' in source
    for forbidden in ("LIVE MARKET P", "LIVE MODEL P", "LIVE EDGE", "Live market P", "Live edge"):
        assert forbidden not in source
