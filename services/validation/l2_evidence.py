from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from string import hexdigits
from typing import Literal

from services.market_data.contracts import ResearchAsset
from services.market_data.l2_corpus_replay import PublicL2CorpusReplay

from .core import PerformanceMetrics
from .experiments import stable_fingerprint
from .l2_baselines import (
    BASELINE_STRATEGIES,
    L2BaselineCampaignConfig,
    L2BaselineValidation,
    run_l2_baseline,
    run_l2_baseline_campaign,
)
from .resampling import MonteCarloSummary

L2BaselineResearchDecision = Literal["CONTROL_ONLY"]


@dataclass(frozen=True, slots=True)
class L2BaselineExperimentEvidence:
    experiment_id: str
    dataset_fingerprint: str
    returns_fingerprint: str
    evidence_fingerprint: str
    campaign_fingerprint: str
    asset: ResearchAsset
    strategy_name: str
    strategy_version: str
    research_decision: L2BaselineResearchDecision
    manifest: dict[str, object]
    validation_plan: dict[str, object]
    validation_result: dict[str, object]
    payload: dict[str, object]
    financial_connectivity: bool = False
    real_money_execution: bool = False


def _normalize_git_sha(value: str) -> str:
    normalized = value.strip().lower()
    if not 7 <= len(normalized) <= 64:
        raise ValueError("git_sha must contain between 7 and 64 hexadecimal characters")
    if any(character not in hexdigits for character in normalized):
        raise ValueError("git_sha must contain only hexadecimal characters")
    return normalized


def _finite_or_none(value: float) -> float | None:
    return value if isfinite(value) else None


def _metrics_payload(metrics: PerformanceMetrics) -> dict[str, object]:
    return {
        "sample_count": metrics.sample_count,
        "cumulative_return": _finite_or_none(metrics.cumulative_return),
        "mean_return": _finite_or_none(metrics.mean_return),
        "volatility": _finite_or_none(metrics.volatility),
        "sharpe": _finite_or_none(metrics.sharpe),
        "sortino": _finite_or_none(metrics.sortino),
        "max_drawdown": _finite_or_none(metrics.max_drawdown),
        "hit_rate": _finite_or_none(metrics.hit_rate),
        "profit_factor": _finite_or_none(metrics.profit_factor),
    }


def _monte_carlo_payload(summary: MonteCarloSummary) -> dict[str, object]:
    return {
        "simulations": summary.simulations,
        "path_length": summary.path_length,
        "block_size": summary.block_size,
        "seed": summary.seed,
        "median_terminal_return": _finite_or_none(summary.median_terminal_return),
        "p05_terminal_return": _finite_or_none(summary.p05_terminal_return),
        "p95_terminal_return": _finite_or_none(summary.p95_terminal_return),
        "median_max_drawdown": _finite_or_none(summary.median_max_drawdown),
        "p95_max_drawdown": _finite_or_none(summary.p95_max_drawdown),
        "probability_of_loss": _finite_or_none(summary.probability_of_loss),
    }


def _validation_payload(
    validation: L2BaselineValidation,
    *,
    pbo: float | None,
    pbo_status: str,
) -> dict[str, object]:
    report = validation.report
    return {
        "performance": _metrics_payload(report.metrics),
        "positive_fold_fraction": _finite_or_none(report.positive_fold_fraction),
        "worst_fold_return": _finite_or_none(report.worst_fold_return),
        "median_fold_return": _finite_or_none(report.median_fold_return),
        "robustness_score": _finite_or_none(report.robustness_score),
        "deflated_sharpe_ratio": _finite_or_none(
            validation.deflated_sharpe_ratio
        ),
        "monte_carlo": _monte_carlo_payload(validation.monte_carlo),
        "negative_controls": {
            "delay": _metrics_payload(validation.delay_control_metrics),
            "timestamp_shuffle": _metrics_payload(
                validation.shuffle_control_metrics
            ),
        },
        "probability_of_backtest_overfitting": _finite_or_none(pbo)
        if pbo is not None
        else None,
        "pbo_status": pbo_status,
    }


def _validation_plan(config: L2BaselineCampaignConfig) -> dict[str, object]:
    return {
        "method": "PURGED_WALK_FORWARD",
        "train_size": config.train_size,
        "test_size": config.test_size,
        "purge_size": config.purge_size,
        "embargo_size": config.embargo_size,
        "step_size": config.step_size,
        "trials": max(config.trials, len(BASELINE_STRATEGIES)),
        "monte_carlo": {
            "simulations": config.monte_carlo_simulations,
            "block_size": config.monte_carlo_block_size,
            "seed": config.monte_carlo_seed,
        },
        "negative_controls": {
            "delay_periods": config.delay_periods,
            "shuffle_seed": config.shuffle_seed,
        },
        "pbo_segments": config.pbo_segments,
    }


def _strategy_parameters(
    strategy_name: str,
    config: L2BaselineCampaignConfig,
) -> dict[str, object]:
    parameters: dict[str, object] = {
        "cost_bps": config.cost_bps,
        "reconnect_position_reset": True,
    }
    if strategy_name == "buy_hold_mid":
        parameters.update(
            {
                "position_rule": "LONG_WITHIN_CONNECTION_GENERATION",
                "signal_lag_periods": 0,
            }
        )
    else:
        parameters.update(
            {
                "signal_source": "PREVIOUS_COMPLETED_MID_RETURN",
                "signal_lag_periods": 1,
            }
        )
    return parameters


def _manifest(
    *,
    dataset: dict[str, object],
    replay_fingerprint: str,
    asset: ResearchAsset,
    strategy_name: str,
    strategy_version: str,
    git_sha: str,
    config: L2BaselineCampaignConfig,
) -> dict[str, object]:
    return {
        "research_mode": "HISTORICAL_REPLAY",
        "dataset": dataset,
        "feature_version": "public-l2-mid-return-v1",
        "strategy_name": strategy_name,
        "strategy_version": strategy_version,
        "model_version": "none",
        "git_sha": git_sha,
        "seed": config.monte_carlo_seed,
        "replay_fingerprint": replay_fingerprint,
        "windows": [
            {
                "role": "TEST",
                "start_at": dataset["start_at"],
                "end_at": dataset["end_at"],
            }
        ],
        "parameters": {
            "asset": asset,
            **_strategy_parameters(strategy_name, config),
        },
        "execution_assumptions": {
            "price_basis": "MID_PRICE_RESEARCH_CONTROL",
            "transaction_cost_model": "TURNOVER_BPS",
            "cost_bps": config.cost_bps,
            "queue_model": "NOT_APPLICABLE_CONTROL",
            "latency_model": "NONE_CONTROL",
            "reconnect_position_reset": True,
        },
    }


def build_l2_baseline_experiment_evidence(
    replay: PublicL2CorpusReplay,
    *,
    asset: ResearchAsset,
    git_sha: str,
    config: L2BaselineCampaignConfig | None = None,
) -> tuple[L2BaselineExperimentEvidence, ...]:
    """Build deterministic, persistible Experiment Registry evidence for L2 controls."""

    resolved_config = config or L2BaselineCampaignConfig()
    normalized_git_sha = _normalize_git_sha(git_sha)
    campaign = run_l2_baseline_campaign(
        replay,
        asset=asset,
        config=resolved_config,
    )
    validation_by_strategy = {
        item.strategy.name: item for item in campaign.validations
    }
    validation_plan = _validation_plan(resolved_config)
    dataset_fingerprint = stable_fingerprint(campaign.dataset)
    output: list[L2BaselineExperimentEvidence] = []

    for spec in BASELINE_STRATEGIES:
        validation = validation_by_strategy[spec.name]
        run = run_l2_baseline(
            replay,
            asset=asset,
            strategy_name=spec.name,
            cost_bps=resolved_config.cost_bps,
        )
        manifest = _manifest(
            dataset=campaign.dataset,
            replay_fingerprint=campaign.replay_fingerprint,
            asset=asset,
            strategy_name=spec.name,
            strategy_version=spec.version,
            git_sha=normalized_git_sha,
            config=resolved_config,
        )
        experiment_id = stable_fingerprint(
            {
                "manifest": manifest,
                "validation_plan": validation_plan,
            }
        )
        returns_fingerprint = stable_fingerprint({"returns": list(run.returns)})
        validation_result = _validation_payload(
            validation,
            pbo=campaign.pbo,
            pbo_status=campaign.pbo_status,
        )
        evidence_core = {
            "experiment_id": experiment_id,
            "returns_fingerprint": returns_fingerprint,
            "validation_result": validation_result,
            "campaign_fingerprint": campaign.campaign_fingerprint,
            "research_decision": "CONTROL_ONLY",
        }
        evidence_fingerprint = stable_fingerprint(evidence_core)
        payload: dict[str, object] = {
            "manifest": manifest,
            "validation_plan": validation_plan,
            "returns_fingerprint": returns_fingerprint,
            "validation_result": validation_result,
            "campaign_fingerprint": campaign.campaign_fingerprint,
            "evidence_fingerprint": evidence_fingerprint,
            "research_decision": {
                "status": "CONTROL_ONLY",
                "promotion_eligible": False,
                "reason": (
                    "Baseline benchmark evidence is a research control and cannot "
                    "be promoted as alpha."
                ),
            },
            "financial_connectivity": False,
            "real_money_execution": False,
        }
        output.append(
            L2BaselineExperimentEvidence(
                experiment_id=experiment_id,
                dataset_fingerprint=dataset_fingerprint,
                returns_fingerprint=returns_fingerprint,
                evidence_fingerprint=evidence_fingerprint,
                campaign_fingerprint=campaign.campaign_fingerprint,
                asset=asset,
                strategy_name=spec.name,
                strategy_version=spec.version,
                research_decision="CONTROL_ONLY",
                manifest=manifest,
                validation_plan=validation_plan,
                validation_result=validation_result,
                payload=payload,
            )
        )

    return tuple(output)


def evidence_manifest_fingerprint(
    evidence: L2BaselineExperimentEvidence,
) -> str:
    """Recompute the registry identity from one evidence record."""

    return stable_fingerprint(
        {
            "manifest": evidence.manifest,
            "validation_plan": evidence.validation_plan,
        }
    )


def evidence_payload_fingerprint(
    evidence: L2BaselineExperimentEvidence,
) -> str:
    """Fingerprint the persisted payload independently of database metadata."""

    return stable_fingerprint(evidence.payload)
