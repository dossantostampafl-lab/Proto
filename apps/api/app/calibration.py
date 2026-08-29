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


def brier_score(observations: list[CalibrationObservation]) -> float:
    if not observations:
        raise ValueError("at least one observation is required")
    return sum((item.probability - item.outcome) ** 2 for item in observations) / len(
        observations
    )


def log_loss(observations: list[CalibrationObservation], epsilon: float = 1e-12) -> float:
    if not observations:
        raise ValueError("at least one observation is required")
    total = 0.0
    for item in observations:
        probability = min(max(item.probability, epsilon), 1.0 - epsilon)
        total -= item.outcome * log(probability) + (1 - item.outcome) * log(1 - probability)
    return total / len(observations)


def calibration_error(
    observations: list[CalibrationObservation],
    bins: int = 10,
) -> float:
    if not observations:
        raise ValueError("at least one observation is required")
    if bins < 2:
        raise ValueError("bins must be at least 2")

    buckets: list[list[CalibrationObservation]] = [[] for _ in range(bins)]
    for item in observations:
        index = min(int(item.probability * bins), bins - 1)
        buckets[index].append(item)

    total = len(observations)
    error = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        confidence = sum(item.probability for item in bucket) / len(bucket)
        frequency = sum(item.outcome for item in bucket) / len(bucket)
        error += (len(bucket) / total) * abs(confidence - frequency)
    return error
