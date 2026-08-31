from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import log


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    mean_probability: float | None
    observed_frequency: float | None
    calibration_error: float | None


@dataclass(frozen=True)
class CalibrationReport:
    count: int
    brier_score: float
    log_loss: float
    expected_calibration_error: float
    maximum_calibration_error: float
    bins: tuple[CalibrationBin, ...]


def _validated_samples(samples: Iterable[tuple[float, int | bool]]) -> list[tuple[float, int]]:
    result: list[tuple[float, int]] = []
    for probability, outcome in samples:
        probability = float(probability)
        if not 0.0 <= probability <= 1.0:
            raise ValueError("probability must be between 0 and 1")
        outcome_value = int(outcome)
        if outcome_value not in (0, 1):
            raise ValueError("outcome must be binary")
        result.append((probability, outcome_value))
    if not result:
        raise ValueError("at least one calibration sample is required")
    return result


def calibration_report(
    samples: Iterable[tuple[float, int | bool]],
    *,
    bin_count: int = 10,
    epsilon: float = 1e-12,
) -> CalibrationReport:
    """Compute deterministic binary-probability quality metrics for research/replay."""
    if bin_count < 2:
        raise ValueError("bin_count must be at least 2")
    if not 0.0 < epsilon < 0.5:
        raise ValueError("epsilon must be between 0 and 0.5")

    observations = _validated_samples(samples)
    count = len(observations)
    brier = sum((probability - outcome) ** 2 for probability, outcome in observations) / count
    log_loss = -sum(
        outcome * log(min(max(probability, epsilon), 1.0 - epsilon))
        + (1 - outcome) * log(min(max(1.0 - probability, epsilon), 1.0 - epsilon))
        for probability, outcome in observations
    ) / count

    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bin_count)]
    for probability, outcome in observations:
        index = min(int(probability * bin_count), bin_count - 1)
        buckets[index].append((probability, outcome))

    bins: list[CalibrationBin] = []
    weighted_error = 0.0
    maximum_error = 0.0
    for index, bucket in enumerate(buckets):
        lower = index / bin_count
        upper = (index + 1) / bin_count
        if not bucket:
            bins.append(CalibrationBin(lower, upper, 0, None, None, None))
            continue
        mean_probability = sum(item[0] for item in bucket) / len(bucket)
        observed_frequency = sum(item[1] for item in bucket) / len(bucket)
        error = abs(mean_probability - observed_frequency)
        weighted_error += error * len(bucket) / count
        maximum_error = max(maximum_error, error)
        bins.append(
            CalibrationBin(
                lower,
                upper,
                len(bucket),
                mean_probability,
                observed_frequency,
                error,
            )
        )

    return CalibrationReport(
        count=count,
        brier_score=brier,
        log_loss=log_loss,
        expected_calibration_error=weighted_error,
        maximum_calibration_error=maximum_error,
        bins=tuple(bins),
    )
