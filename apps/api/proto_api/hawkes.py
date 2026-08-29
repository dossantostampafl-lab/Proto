from __future__ import annotations

from dataclasses import dataclass
from math import exp


@dataclass(frozen=True)
class HawkesState:
    baseline_intensity: float
    current_intensity: float
    excitation: float
    decay: float
    branching_ratio: float
    event_probability: float


def exponential_hawkes_state(
    *,
    now: float,
    event_times: list[float],
    mu: float,
    alpha: float,
    beta: float,
    horizon: float = 1.0,
) -> HawkesState:
    if mu < 0 or alpha < 0 or beta <= 0 or horizon <= 0:
        raise ValueError("invalid Hawkes parameters")
    if alpha >= beta:
        raise ValueError("unstable Hawkes process: alpha/beta must be < 1")
    if any(event_time > now for event_time in event_times):
        raise ValueError("event times cannot be in the future")

    excitation = sum(alpha * exp(-beta * (now - event_time)) for event_time in event_times)
    intensity = mu + excitation
    event_probability = 1.0 - exp(-intensity * horizon)
    return HawkesState(
        baseline_intensity=mu,
        current_intensity=intensity,
        excitation=excitation,
        decay=beta,
        branching_ratio=alpha / beta,
        event_probability=event_probability,
    )
