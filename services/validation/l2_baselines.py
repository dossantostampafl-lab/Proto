from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from math import isfinite
from typing import Literal

from services.market_data.contracts import ResearchAsset
from services.market_data.l2_corpus_replay import (
    PublicL2CorpusReplay,
    PublicL2ReplaySnapshot,
)

from .core import (
    PerformanceMetrics,
    ValidationReport,
    performance_metrics,
    purged_walk_forward_splits,
    validation_report,
)
from .experiments import stable_fingerprint
from .overfitting import deflated_sharpe_ratio, probability_of_backtest_overfitting
from .perturbation import delay_signal, timestamp_shuffle
from .resampling import MonteCarloSummary, monte_carlo_block_bootstrap

BaselineStrategyName = Literal[
    "buy_hold_mid",
    "momentum_1",
    "mean_reversion_1",
]


@dataclass(frozen=True, slots=True)
class L2BaselineSpec:
    name: BaselineStrategyName
    version: str
    description: str


BASELINE_STRATEGIES: tuple[L2BaselineSpec, ...] = (
    L2BaselineSpec(
        name="buy_hold_mid",
        version="v1",
        description="Long mid-price benchmark within each verified connection generation.",
    ),
    L2BaselineSpec(
        name="momentum_1",
        version="v1",
        description=(
            "One-step lagged momentum benchmark; current return is never used "
            "to form its own signal."
        ),
    ),
    L2BaselineSpec(
        name="mean_reversion_1",
        version="v1",
        description=(
            "One-step lagged contrarian benchmark; current return is never used "
            "to form its own signal."
        ),
    ),
)

_BASELINE_BY_NAME = {item.name: item for item in BASELINE_STRATEGIES}


@dataclass(frozen=True, slots=True)
class L2MarketReturnSample:
    observed_at: datetime
    connection_generation: int
    market_return: float
    previous_market_return: float | None


@dataclass(frozen=True, slots=True)
class L2BaselineReturnSample:
    observed_at: datetime
    connection_generation: int
    market_return: float
    previous_market_return: float | None
    position: float
    turnover: float
    transaction_cost: float
    net_return: float


@dataclass(frozen=True, slots=True)
class L2BaselineRun:
    strategy: L2BaselineSpec
    asset: ResearchAsset
    cost_bps: float
    samples: tuple[L2BaselineReturnSample, ...]

    @property
    def returns(self) -> tuple[float, ...]:
        return tuple(sample.net_return for sample in self.samples)

    @property
    def positions(self) -> tuple[float, ...]:
        return tuple(sample.position for sample in self.samples)


@dataclass(frozen=True, slots=True)
class L2BaselineValidation:
    strategy: L2BaselineSpec
    report: ValidationReport
    deflated_sharpe_ratio: float
    monte_carlo: MonteCarloSummary
    delay_control_metrics: PerformanceMetrics
    shuffle_control_metrics: PerformanceMetrics


@dataclass(frozen=True, slots=True)
class L2BaselineCampaignConfig:
    cost_bps: float = 5.0
    train_size: int = 6
    test_size: int = 3
    purge_size: int = 0
    embargo_size: int = 0
    step_size: int | None = None
    trials: int = 3
    monte_carlo_simulations: int = 100
    monte_carlo_block_size: int = 2
    monte_carlo_seed: int = 13
    delay_periods: int = 1
    shuffle_seed: int = 17
    pbo_segments: int = 4

    def __post_init__(self) -> None:
        if not isfinite(self.cost_bps) or not 0.0 <= self.cost_bps <= 1_000.0:
            raise ValueError("cost_bps must be finite and between 0 and 1000")
        if self.train_size <= 0 or self.test_size <= 0:
            raise ValueError("train_size and test_size must be positive")
        if self.purge_size < 0 or self.embargo_size < 0:
            raise ValueError("purge_size and embargo_size must be non-negative")
        if self.step_size is not None and self.step_size <= 0:
            raise ValueError("step_size must be positive when provided")
        if self.trials <= 0:
            raise ValueError("trials must be positive")
        if self.monte_carlo_simulations < 10:
            raise ValueError("monte_carlo_simulations must be at least 10")
        if self.monte_carlo_block_size <= 0:
            raise ValueError("monte_carlo_block_size must be positive")
        if self.delay_periods < 0:
            raise ValueError("delay_periods must be non-negative")
        if self.pbo_segments < 4 or self.pbo_segments % 2 != 0:
            raise ValueError("pbo_segments must be an even integer >= 4")


@dataclass(frozen=True, slots=True)
class L2BaselineCampaignResult:
    asset: ResearchAsset
    dataset: dict[str, object]
    replay_fingerprint: str
    market_sample_count: int
    validations: tuple[L2BaselineValidation, ...]
    pbo: float | None
    pbo_status: Literal["COMPUTED", "INSUFFICIENT_SAMPLE"]
    campaign_fingerprint: str
    financial_connectivity: bool = False
    real_money_execution: bool = False


def available_l2_baselines() -> tuple[L2BaselineSpec, ...]:
    return BASELINE_STRATEGIES


def _mid_price(item: PublicL2ReplaySnapshot) -> float:
    best_bid = item.snapshot.bids[0].price
    best_ask = item.snapshot.asks[0].price
    mid = (best_bid + best_ask) / 2.0
    if not isfinite(mid) or mid <= 0.0:
        raise ValueError("L2 replay mid-price must be positive and finite")
    return mid


def build_l2_market_returns(
    replay: PublicL2CorpusReplay,
    *,
    asset: ResearchAsset,
) -> tuple[L2MarketReturnSample, ...]:
    """Build mid-price returns without crossing asset or reconnect boundaries."""

    selected = tuple(item for item in replay.run_all() if item.snapshot.asset == asset)
    if not selected:
        raise ValueError(f"verified L2 corpus contains no snapshots for {asset}")

    output: list[L2MarketReturnSample] = []
    previous_mid: float | None = None
    previous_generation: int | None = None
    previous_return: float | None = None

    for item in selected:
        mid = _mid_price(item)
        if item.connection_generation != previous_generation:
            previous_mid = mid
            previous_generation = item.connection_generation
            previous_return = None
            continue
        if previous_mid is None:
            raise RuntimeError("L2 baseline return state is inconsistent")
        current_return = mid / previous_mid - 1.0
        if not isfinite(current_return) or current_return <= -1.0:
            raise ValueError("L2 market return is outside supported bounds")
        output.append(
            L2MarketReturnSample(
                observed_at=item.snapshot.observed_at,
                connection_generation=item.connection_generation,
                market_return=current_return,
                previous_market_return=previous_return,
            )
        )
        previous_mid = mid
        previous_return = current_return

    if not output:
        raise ValueError("verified L2 corpus does not contain consecutive snapshots")
    return tuple(output)


def _position(
    strategy: BaselineStrategyName,
    previous_market_return: float | None,
) -> float:
    if strategy == "buy_hold_mid":
        return 1.0
    if previous_market_return is None or previous_market_return == 0.0:
        return 0.0
    direction = 1.0 if previous_market_return > 0.0 else -1.0
    if strategy == "momentum_1":
        return direction
    if strategy == "mean_reversion_1":
        return -direction
    raise ValueError(f"unsupported L2 baseline strategy: {strategy}")


def _net_samples_from_positions(
    market_samples: tuple[L2MarketReturnSample, ...],
    positions: tuple[float, ...],
    *,
    cost_bps: float,
) -> tuple[L2BaselineReturnSample, ...]:
    if len(market_samples) != len(positions):
        raise ValueError("market samples and positions must have equal length")
    cost_rate = cost_bps / 10_000.0
    previous_position = 0.0
    previous_generation: int | None = None
    output: list[L2BaselineReturnSample] = []

    for sample, position in zip(market_samples, positions, strict=True):
        if sample.connection_generation != previous_generation:
            previous_position = 0.0
            previous_generation = sample.connection_generation
        turnover = abs(position - previous_position)
        transaction_cost = turnover * cost_rate
        net_return = position * sample.market_return - transaction_cost
        if net_return <= -1.0 or not isfinite(net_return):
            raise ValueError("cost-adjusted baseline return is outside supported bounds")
        output.append(
            L2BaselineReturnSample(
                observed_at=sample.observed_at,
                connection_generation=sample.connection_generation,
                market_return=sample.market_return,
                previous_market_return=sample.previous_market_return,
                position=position,
                turnover=turnover,
                transaction_cost=transaction_cost,
                net_return=net_return,
            )
        )
        previous_position = position
    return tuple(output)


def run_l2_baseline(
    replay: PublicL2CorpusReplay,
    *,
    asset: ResearchAsset,
    strategy_name: BaselineStrategyName,
    cost_bps: float,
) -> L2BaselineRun:
    if strategy_name not in _BASELINE_BY_NAME:
        raise ValueError(f"unsupported L2 baseline strategy: {strategy_name}")
    if not isfinite(cost_bps) or not 0.0 <= cost_bps <= 1_000.0:
        raise ValueError("cost_bps must be finite and between 0 and 1000")
    market_samples = build_l2_market_returns(replay, asset=asset)
    positions = tuple(
        _position(strategy_name, sample.previous_market_return) for sample in market_samples
    )
    samples = _net_samples_from_positions(
        market_samples,
        positions,
        cost_bps=cost_bps,
    )
    return L2BaselineRun(
        strategy=_BASELINE_BY_NAME[strategy_name],
        asset=asset,
        cost_bps=cost_bps,
        samples=samples,
    )


def _control_metrics(
    market_samples: tuple[L2MarketReturnSample, ...],
    positions: tuple[float, ...],
    *,
    cost_bps: float,
) -> PerformanceMetrics:
    samples = _net_samples_from_positions(
        market_samples,
        positions,
        cost_bps=cost_bps,
    )
    return performance_metrics(tuple(item.net_return for item in samples))


def run_l2_baseline_campaign(
    replay: PublicL2CorpusReplay,
    *,
    asset: ResearchAsset,
    config: L2BaselineCampaignConfig | None = None,
) -> L2BaselineCampaignResult:
    """Run deterministic baseline controls over a verified public L2 corpus."""

    resolved_config = config or L2BaselineCampaignConfig()
    market_samples = build_l2_market_returns(replay, asset=asset)
    market_sample_count = len(market_samples)
    if resolved_config.monte_carlo_block_size > market_sample_count:
        raise ValueError("monte_carlo_block_size exceeds L2 market sample count")

    folds = purged_walk_forward_splits(
        market_sample_count,
        train_size=resolved_config.train_size,
        test_size=resolved_config.test_size,
        purge_size=resolved_config.purge_size,
        embargo_size=resolved_config.embargo_size,
        step_size=resolved_config.step_size,
    )

    runs = tuple(
        run_l2_baseline(
            replay,
            asset=asset,
            strategy_name=spec.name,
            cost_bps=resolved_config.cost_bps,
        )
        for spec in BASELINE_STRATEGIES
    )
    validations: list[L2BaselineValidation] = []
    trials = max(resolved_config.trials, len(runs))

    for run in runs:
        report = validation_report(run.returns, folds)
        delayed_positions = tuple(
            delay_signal(
                run.positions,
                resolved_config.delay_periods,
                fill_value=0.0,
            )
        )
        shuffled_positions = tuple(
            timestamp_shuffle(
                run.positions,
                seed=resolved_config.shuffle_seed,
            )
        )
        validations.append(
            L2BaselineValidation(
                strategy=run.strategy,
                report=report,
                deflated_sharpe_ratio=deflated_sharpe_ratio(
                    run.returns,
                    trials=trials,
                ),
                monte_carlo=monte_carlo_block_bootstrap(
                    run.returns,
                    simulations=resolved_config.monte_carlo_simulations,
                    block_size=resolved_config.monte_carlo_block_size,
                    seed=resolved_config.monte_carlo_seed,
                ),
                delay_control_metrics=_control_metrics(
                    market_samples,
                    delayed_positions,
                    cost_bps=resolved_config.cost_bps,
                ),
                shuffle_control_metrics=_control_metrics(
                    market_samples,
                    shuffled_positions,
                    cost_bps=resolved_config.cost_bps,
                ),
            )
        )

    pbo_sample_compatible = (
        market_sample_count >= resolved_config.pbo_segments
        and market_sample_count % resolved_config.pbo_segments == 0
    )
    if pbo_sample_compatible:
        pbo = probability_of_backtest_overfitting(
            tuple(run.returns for run in runs),
            segments=resolved_config.pbo_segments,
        )
        pbo_status: Literal["COMPUTED", "INSUFFICIENT_SAMPLE"] = "COMPUTED"
    else:
        pbo = None
        pbo_status = "INSUFFICIENT_SAMPLE"

    provenance = replay.experiment_provenance(
        seed=resolved_config.monte_carlo_seed
    )
    fingerprint_payload = {
        "asset": asset,
        "dataset": provenance["dataset"],
        "replay_fingerprint": provenance["replay_fingerprint"],
        "config": asdict(resolved_config),
        "strategies": [
            {
                "name": run.strategy.name,
                "version": run.strategy.version,
                "returns": list(run.returns),
            }
            for run in runs
        ],
        "pbo": pbo,
        "pbo_status": pbo_status,
    }
    return L2BaselineCampaignResult(
        asset=asset,
        dataset=provenance["dataset"],
        replay_fingerprint=str(provenance["replay_fingerprint"]),
        market_sample_count=market_sample_count,
        validations=tuple(validations),
        pbo=pbo,
        pbo_status=pbo_status,
        campaign_fingerprint=stable_fingerprint(fingerprint_payload),
    )
