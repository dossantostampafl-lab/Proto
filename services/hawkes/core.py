from __future__ import annotations

from dataclasses import dataclass
from math import exp


@dataclass(frozen=True)
class HawkesEstimate:
    baseline_intensity: float
    current_intensity: float
    excitation: float
    decay: float
    branching_ratio: float
    event_probability: float


class ExponentialHawkesEngine:
    def __init__(self, *, mu: float, alpha: float, beta: float) -> None:
        if mu < 0:
            raise ValueError("mu must be non-negative")
        if alpha < 0:
            raise ValueError("alpha must be non-negative")
        if beta <= 0:
            raise ValueError("beta must be positive")
        if alpha >= beta:
            raise ValueError("alpha/beta must be below 1 for a stable process")
        self.mu = mu
        self.alpha = alpha
        self.beta = beta
        self._event_times: list[float] = []

    @property
    def branching_ratio(self) -> float:
        return self.alpha / self.beta

    def reset(self) -> None:
        self._event_times.clear()

    def record(self, timestamp: float) -> None:
        if self._event_times and timestamp < self._event_times[-1]:
            raise ValueError("events must be recorded in non-decreasing time order")
        self._event_times.append(timestamp)

    def intensity(self, timestamp: float) -> float:
        excitation = sum(
            self.alpha * exp(-self.beta * (timestamp - event_time))
            for event_time in self._event_times
            if event_time <= timestamp
        )
        return self.mu + excitation

    def estimate(self, *, timestamp: float, horizon: float = 1.0) -> HawkesEstimate:
        if horizon <= 0:
            raise ValueError("horizon must be positive")
        current_intensity = self.intensity(timestamp)
        excitation = current_intensity - self.mu
        event_probability = 1.0 - exp(-current_intensity * horizon)
        return HawkesEstimate(
            baseline_intensity=self.mu,
            current_intensity=current_intensity,
            excitation=excitation,
            decay=self.beta,
            branching_ratio=self.branching_ratio,
            event_probability=event_probability,
        )
