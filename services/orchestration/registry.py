from __future__ import annotations

from dataclasses import dataclass

from .runtime import JobCapability, JobSpec

CATALOG_VERSION = "2026-09-03.1"


@dataclass(frozen=True, slots=True)
class JobContract:
    spec: JobSpec
    input_contract: str
    output_contract: str
    completion_criteria: tuple[str, ...]
    owner_domain: str


def build_job_catalog() -> dict[str, JobContract]:
    contracts = (
        JobContract(
            spec=JobSpec(
                "market-data-health",
                JobCapability.MARKET_DATA,
                allowed_modes=frozenset({"LIVE_MONITORING"}),
            ),
            input_contract="current public-feed health snapshot",
            output_contract="normalized health/freshness assessment",
            completion_criteria=(
                "BTC/ETH/SOL freshness evaluated",
                "source provenance preserved",
                "no financial side effects",
            ),
            owner_domain="market-data",
        ),
        JobContract(
            spec=JobSpec(
                "opportunity-scan",
                JobCapability.QUANT_RESEARCH,
                allowed_modes=frozenset(
                    {
                        "SIMULATION",
                        "SHADOW",
                        "PAPER_TRADING",
                        "HISTORICAL_REPLAY",
                        "LIVE_MONITORING",
                    }
                ),
            ),
            input_contract=(
                "fact-only multiasset observations plus explicit regime/ranking policies"
            ),
            output_contract=(
                "classified market states and ranked opportunities with incomplete evidence omitted"
            ),
            completion_criteria=(
                "all thresholds and weights supplied explicitly",
                "incomplete quantitative evidence omitted rather than synthesized",
                "instrument and provenance fields preserved",
                "no financial side effects",
            ),
            owner_domain="analytics",
        ),
        JobContract(
            spec=JobSpec(
                "shadow-decision",
                JobCapability.QUANT_RESEARCH,
                allowed_modes=frozenset({"SHADOW"}),
            ),
            input_contract=(
                "fact-only decision lineage plus an explicit SimulationRequest built from observed "
                "market data"
            ),
            output_contract=(
                "authoritative SHADOW risk/fill evaluation and persisted SHADOW_ONLY decision lineage"
            ),
            completion_criteria=(
                "candidate contains no fabricated model or market fields",
                "server-authoritative exposure, liquidity and configured risk limits evaluated",
                "hypothetical fill is never persisted or applied to the paper portfolio",
                "actual_action remains null and decision lineage is persisted before success",
                "financial connectivity and real-money execution remain unavailable",
            ),
            owner_domain="orchestration",
        ),
        JobContract(
            spec=JobSpec(
                "quant-research",
                JobCapability.QUANT_RESEARCH,
                allowed_modes=frozenset({"SIMULATION", "SHADOW", "HISTORICAL_REPLAY"}),
            ),
            input_contract=(
                "versioned market/replay observations and explicit execution-cost inputs"
            ),
            output_contract="probability/fair-value/edge research result with lineage",
            completion_criteria=(
                "input lineage persisted",
                "cost provenance explicit",
                "model output persisted or returned with version metadata",
            ),
            owner_domain="quant",
        ),
        JobContract(
            spec=JobSpec(
                "model-calibration",
                JobCapability.CALIBRATION,
                allowed_modes=frozenset({"SIMULATION", "SHADOW", "HISTORICAL_REPLAY"}),
            ),
            input_contract="labeled probability observations and model identifier",
            output_contract="Brier/log-loss/ECE/MCE/reliability metrics",
            completion_criteria=(
                "labels are present",
                "metrics computed without synthetic substitution",
                "calibration record persisted",
            ),
            owner_domain="calibration",
        ),
        JobContract(
            spec=JobSpec(
                "risk-evaluation",
                JobCapability.RISK,
                allowed_modes=frozenset({"SIMULATION", "SHADOW", "PAPER_TRADING"}),
            ),
            input_contract="candidate action, exposure, liquidity and configured limits",
            output_contract="independent approved/rejected risk decision",
            completion_criteria=(
                "all configured limits evaluated",
                "decision reason recorded",
                "failure resolves to rejection",
            ),
            owner_domain="risk",
        ),
        JobContract(
            spec=JobSpec(
                "historical-replay",
                JobCapability.REPLAY,
                allowed_modes=frozenset({"HISTORICAL_REPLAY"}),
            ),
            input_contract="immutable replay dataset/session definition",
            output_contract="deterministic replay result and event lineage",
            completion_criteria=(
                "session identifiers persisted",
                "event ordering preserved",
                "result reproducible from recorded inputs",
            ),
            owner_domain="replay",
        ),
        JobContract(
            spec=JobSpec(
                "portfolio-snapshot",
                JobCapability.PORTFOLIO,
                allowed_modes=frozenset({"SIMULATION", "PAPER_TRADING"}),
            ),
            input_contract="paper/simulation fills, positions and marks",
            output_contract="portfolio/P&L/risk snapshot",
            completion_criteria=(
                "positions reconciled from journal",
                "P&L attribution computed from recorded fills/marks",
                "source labeled PAPER/SIM",
            ),
            owner_domain="portfolio",
        ),
        JobContract(
            spec=JobSpec(
                "observability-health",
                JobCapability.OBSERVABILITY,
                allowed_modes=frozenset(
                    {
                        "SIMULATION",
                        "SHADOW",
                        "PAPER_TRADING",
                        "HISTORICAL_REPLAY",
                        "LIVE_MONITORING",
                    }
                ),
            ),
            input_contract="runtime health, event journal, persistence and feed metrics",
            output_contract="operational health assessment",
            completion_criteria=(
                "critical dependencies evaluated",
                "staleness and journal integrity represented",
                "degraded state never reported as healthy",
            ),
            owner_domain="observability",
        ),
        JobContract(
            spec=JobSpec(
                "paper-decision",
                JobCapability.PAPER_EXECUTION,
                requires_risk_gate=True,
                allowed_modes=frozenset({"PAPER_TRADING"}),
            ),
            input_contract="paper-only candidate action and independent risk context",
            output_contract="paper execution decision/result",
            completion_criteria=(
                "independent risk gate approved before handler invocation",
                "event journal contains decision/result",
                "real-money execution remains unavailable",
            ),
            owner_domain="paper-execution",
        ),
    )
    return {contract.spec.name: contract for contract in contracts}


JOB_CATALOG = build_job_catalog()
