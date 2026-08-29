from __future__ import annotations

from math import log

from pydantic import BaseModel, Field


class CalibrationObservation(BaseModel):
    predicted_probability: float = Field(ge=0.0, le=1.0)
    outcome: int = Field(ge=0, le=1)


class CalibrationBatch(BaseModel):
    observations: list[CalibrationObservation] = Field(min_length=1)


class CalibrationMetrics(BaseModel):
    count: int
    brier_score: float
    log_loss: float
    mean_prediction: float
    observed_frequency: float
    calibration_bias: float


def score_calibration(batch: CalibrationBatch) -> CalibrationMetrics:
    observations = batch.observations
    count = len(observations)
    epsilon = 1e-12

    brier = sum(
        (observation.predicted_probability - observation.outcome) ** 2
        for observation in observations
    ) / count

    log_loss = -sum(
        observation.outcome
        * log(min(max(observation.predicted_probability, epsilon), 1.0 - epsilon))
        + (1 - observation.outcome)
        * log(min(max(1.0 - observation.predicted_probability, epsilon), 1.0 - epsilon))
        for observation in observations
    ) / count

    mean_prediction = sum(
        observation.predicted_probability for observation in observations
    ) / count
    observed_frequency = sum(observation.outcome for observation in observations) / count

    return CalibrationMetrics(
        count=count,
        brier_score=round(brier, 10),
        log_loss=round(log_loss, 10),
        mean_prediction=round(mean_prediction, 10),
        observed_frequency=round(observed_frequency, 10),
        calibration_bias=round(mean_prediction - observed_frequency, 10),
    )
