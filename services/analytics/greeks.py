from __future__ import annotations

from dataclasses import dataclass

from services.quant.core import estimate_probability


@dataclass(frozen=True)
class SyntheticGreeks:
    market_probability_delta: float
    volatility_vega: float
    imbalance_kappa: float
    time_theta: float
    bump_size: float
    model_version: str
    feature_version: str


def _probability(
    *,
    market_probability: float,
    volatility: float,
    imbalance: float,
) -> float:
    return estimate_probability(
        market_probability=market_probability,
        volatility=volatility,
        imbalance=imbalance,
    ).probability


def _central_difference(
    plus: float,
    minus: float,
    bump: float,
) -> float:
    return (plus - minus) / (2.0 * bump)


def calculate_synthetic_greeks(
    *,
    market_probability: float,
    volatility: float,
    imbalance: float,
    bump_size: float = 1e-4,
) -> SyntheticGreeks:
    if not 0.0 < bump_size < 0.1:
        raise ValueError("bump_size must be between zero and 0.1")

    probability_plus = min(market_probability + bump_size, 1.0 - 1e-6)
    probability_minus = max(market_probability - bump_size, 1e-6)
    probability_bump = (probability_plus - probability_minus) / 2.0
    market_probability_delta = _central_difference(
        _probability(
            market_probability=probability_plus,
            volatility=volatility,
            imbalance=imbalance,
        ),
        _probability(
            market_probability=probability_minus,
            volatility=volatility,
            imbalance=imbalance,
        ),
        probability_bump,
    )

    volatility_plus = volatility + bump_size
    volatility_minus = max(volatility - bump_size, 0.0)
    volatility_bump = (volatility_plus - volatility_minus) / 2.0
    volatility_vega = _central_difference(
        _probability(
            market_probability=market_probability,
            volatility=volatility_plus,
            imbalance=imbalance,
        ),
        _probability(
            market_probability=market_probability,
            volatility=volatility_minus,
            imbalance=imbalance,
        ),
        volatility_bump,
    )

    imbalance_plus = min(imbalance + bump_size, 1.0)
    imbalance_minus = max(imbalance - bump_size, -1.0)
    imbalance_bump = (imbalance_plus - imbalance_minus) / 2.0
    imbalance_kappa = _central_difference(
        _probability(
            market_probability=market_probability,
            volatility=volatility,
            imbalance=imbalance_plus,
        ),
        _probability(
            market_probability=market_probability,
            volatility=volatility,
            imbalance=imbalance_minus,
        ),
        imbalance_bump,
    )

    estimate = estimate_probability(
        market_probability=market_probability,
        volatility=volatility,
        imbalance=imbalance,
    )
    return SyntheticGreeks(
        market_probability_delta=market_probability_delta,
        volatility_vega=volatility_vega,
        imbalance_kappa=imbalance_kappa,
        time_theta=0.0,
        bump_size=bump_size,
        model_version=estimate.model_version,
        feature_version=estimate.feature_version,
    )
