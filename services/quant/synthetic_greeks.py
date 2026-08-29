from __future__ import annotations

from dataclasses import dataclass
from math import erf, exp, isfinite, log, sqrt


@dataclass(frozen=True, slots=True)
class BinaryContractInputs:
    spot: float
    strike: float
    volatility: float
    time_to_expiry_years: float

    def validate(self) -> None:
        values = {
            "spot": self.spot,
            "strike": self.strike,
            "volatility": self.volatility,
            "time_to_expiry_years": self.time_to_expiry_years,
        }
        for name, value in values.items():
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")
            if value <= 0.0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class SyntheticGreeks:
    fair_probability: float
    delta: float
    gamma: float
    vega: float
    theta_per_year: float


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def threshold_probability(inputs: BinaryContractInputs) -> float:
    """Research-only probability that a lognormal terminal price finishes above strike."""
    inputs.validate()
    sigma_sqrt_t = inputs.volatility * sqrt(inputs.time_to_expiry_years)
    z_score = (
        log(inputs.spot / inputs.strike)
        - 0.5 * inputs.volatility * inputs.volatility * inputs.time_to_expiry_years
    ) / sigma_sqrt_t
    probability = _normal_cdf(z_score)
    if not isfinite(probability):
        raise ValueError("threshold probability must be finite")
    return min(max(probability, 0.0), 1.0)


def synthetic_greeks(inputs: BinaryContractInputs) -> SyntheticGreeks:
    """Finite-difference sensitivities of fair probability for research and replay."""
    inputs.validate()
    base = threshold_probability(inputs)

    spot_step = min(max(inputs.spot * 1e-4, 1e-12), inputs.spot * 0.25)
    vol_step = max(inputs.volatility * 1e-4, 1e-6)
    time_step = min(
        max(inputs.time_to_expiry_years * 1e-4, 1e-7),
        inputs.time_to_expiry_years * 0.25,
    )

    up_spot = threshold_probability(
        BinaryContractInputs(
            spot=inputs.spot + spot_step,
            strike=inputs.strike,
            volatility=inputs.volatility,
            time_to_expiry_years=inputs.time_to_expiry_years,
        )
    )
    down_spot = threshold_probability(
        BinaryContractInputs(
            spot=inputs.spot - spot_step,
            strike=inputs.strike,
            volatility=inputs.volatility,
            time_to_expiry_years=inputs.time_to_expiry_years,
        )
    )
    delta = (up_spot - down_spot) / (2.0 * spot_step)
    gamma = (up_spot - 2.0 * base + down_spot) / (spot_step * spot_step)

    up_vol = threshold_probability(
        BinaryContractInputs(
            spot=inputs.spot,
            strike=inputs.strike,
            volatility=inputs.volatility + vol_step,
            time_to_expiry_years=inputs.time_to_expiry_years,
        )
    )
    down_volatility = max(inputs.volatility - vol_step, inputs.volatility * 0.5)
    down_vol = threshold_probability(
        BinaryContractInputs(
            spot=inputs.spot,
            strike=inputs.strike,
            volatility=down_volatility,
            time_to_expiry_years=inputs.time_to_expiry_years,
        )
    )
    vega_denominator = (inputs.volatility + vol_step) - down_volatility
    vega = (up_vol - down_vol) / vega_denominator

    later = threshold_probability(
        BinaryContractInputs(
            spot=inputs.spot,
            strike=inputs.strike,
            volatility=inputs.volatility,
            time_to_expiry_years=inputs.time_to_expiry_years + time_step,
        )
    )
    earlier_time = max(
        inputs.time_to_expiry_years - time_step,
        inputs.time_to_expiry_years * 0.5,
    )
    earlier = threshold_probability(
        BinaryContractInputs(
            spot=inputs.spot,
            strike=inputs.strike,
            volatility=inputs.volatility,
            time_to_expiry_years=earlier_time,
        )
    )
    theta = (later - earlier) / ((inputs.time_to_expiry_years + time_step) - earlier_time)

    sensitivities = (delta, gamma, vega, theta)
    if not all(isfinite(value) for value in sensitivities):
        raise ValueError("synthetic sensitivities must be finite")

    return SyntheticGreeks(
        fair_probability=base,
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta_per_year=theta,
    )


def probability_odds(probability: float) -> float:
    if not isfinite(probability) or not 0.0 < probability < 1.0:
        raise ValueError("probability must be finite and strictly between zero and one")
    return probability / (1.0 - probability)


def log_odds(probability: float) -> float:
    return log(probability_odds(probability))


def probability_from_log_odds(value: float) -> float:
    if not isfinite(value):
        raise ValueError("log-odds value must be finite")
    if value >= 0.0:
        return 1.0 / (1.0 + exp(-value))
    exp_value = exp(value)
    return exp_value / (1.0 + exp_value)
