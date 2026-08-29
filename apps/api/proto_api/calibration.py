from __future__ import annotations

from dataclasses import dataclass
from math import log


_EPS = 1e-12


@dataclass(frozen=True)
class CalibrationBucket:
    lower: float
    upper: float
    count: int
    mean_prediction: float
    observed_rate: float


def brier_score(probabilities: list[float], outcomes: list[int]) -> float:
    _validate(probabilities, outcomes)
    return sum((p - y) ** 2 for p, y in zip(probabilities, outcomes, strict=True)) / len(outcomes)


def log_loss(probabilities: list[float], outcomes: list[int]) -> float:
    _validate(probabilities, outcomes)
    total = 0.0
    for probability, outcome in zip(probabilities, outcomes, strict=True):
        p = min(max(probability, _EPS), 1.0 - _EPS)
        total += -(outcome * log(p) + (1 - outcome) * log(1.0 - p))
    return total / len(outcomes)


def calibration_buckets(
    probabilities: list[float], outcomes: list[int], bucket_count: int = 10
) -> list[CalibrationBucket]:
    _validate(probabilities, outcomes)
    if bucket_count <= 0:
        raise ValueError("bucket_count must be positive")

    buckets: list[CalibrationBucket] = []
    for index in range(bucket_count):
        lower = index / bucket_count
        upper = (index + 1) / bucket_count
        members = [
            (p, y)
            for p, y in zip(probabilities, outcomes, strict=True)
            if lower <= p < upper or (index == bucket_count - 1 and p == 1.0)
        ]
        if not members:
            continue
        mean_prediction = sum(p for p, _ in members) / len(members)
        observed_rate = sum(y for _, y in members) / len(members)
        buckets.append(
            CalibrationBucket(
                lower=lower,
                upper=upper,
                count=len(members),
                mean_prediction=mean_prediction,
                observed_rate=observed_rate,
            )
        )
    return buckets


def expected_calibration_error(
    probabilities: list[float], outcomes: list[int], bucket_count: int = 10
) -> float:
    buckets = calibration_buckets(probabilities, outcomes, bucket_count)
    total = len(outcomes)
    return sum(
        (bucket.count / total) * abs(bucket.mean_prediction - bucket.observed_rate)
        for bucket in buckets
    )


def _validate(probabilities: list[float], outcomes: list[int]) -> None:
    if not probabilities or len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must be non-empty and have equal length")
    if any(probability < 0.0 or probability > 1.0 for probability in probabilities):
        raise ValueError("probabilities must be within [0, 1]")
    if any(outcome not in (0, 1) for outcome in outcomes):
        raise ValueError("outcomes must be binary")
