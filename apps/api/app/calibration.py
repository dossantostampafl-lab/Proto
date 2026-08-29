from __future__ import annotations

from dataclasses import dataclass
from math import log


@dataclass(frozen=True)
class CalibrationObservation:
    probability: float
    outcome: int

    def __post_init__(self) -> None:
        if not 0.0 <= self.probability <= 1.0:
            raise ValueError("probability must be within [0, 1]")
        if self.outcome not in (0, 1):
            raise ValueError("outcome must be 0 or 1")


@dataclass(frozen=True)
class CalibrationBucket:
    lower_bound: float
    upper_bound: float
    count: int
    mean_prediction: float
    observed_frequency: float
    absolute_gap: float


def _validate(observations: list[CalibrationObservation], bins: int | None = None) -> None:
    if not observations:
        raise ValueError("at least one observation is required")
    if bins is not None and bins < 2:
        raise ValueError("bins must be at least 2")


def brier_score(observations: list[CalibrationObservation]) -> float:
    _validate(observations)
    return sum((item.probability - item.outcome) ** 2 for item in observations) / len(
        observations
    )


def log_loss(observations: list[CalibrationObservation], epsilon: float = 1e-12) -> float:
    _validate(observations)
    total = 0.0
    for item in observations:
        probability = min(max(item.probability, epsilon), 1.0 - epsilon)
        total -= item.outcome * log(probability) + (1 - item.outcome) * log(1 - probability)
    return total / len(observations)


def reliability_curve(
    observations: list[CalibrationObservation],
    bins: int = 10,
) -> list[CalibrationBucket]:
    _validate(observations, bins)
    bucketed: list[list[CalibrationObservation]] = [[] for _ in range(bins)]
    for item in observations:
        index = min(int(item.probability * bins), bins - 1)
        bucketed[index].append(item)

    result: list[CalibrationBucket] = []
    for index, bucket in enumerate(bucketed):
        if not bucket:
            continue
        mean_prediction = sum(item.probability for item in bucket) / len(bucket)
        observed_frequency = sum(item.outcome for item in bucket) / len(bucket)
        result.append(
            CalibrationBucket(
                lower_bound=index / bins,
                upper_bound=(index + 1) / bins,
                count=len(bucket),
                mean_prediction=mean_prediction,
                observed_frequency=observed_frequency,
                absolute_gap=abs(mean_prediction - observed_frequency),
            )
        )
    return result


def calibration_error(
    observations: list[CalibrationObservation],
    bins: int = 10,
) -> float:
    curve = reliability_curve(observations, bins)
    total = len(observations)
    return sum((bucket.count / total) * bucket.absolute_gap for bucket in curve)
