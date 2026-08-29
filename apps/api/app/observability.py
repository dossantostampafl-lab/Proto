from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from time import perf_counter


@dataclass
class RuntimeMetrics:
    counters: Counter[str] = field(default_factory=Counter)
    latency_ms_total: float = 0.0
    latency_samples: int = 0

    def increment(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    def observe_latency_ms(self, value: float) -> None:
        self.latency_ms_total += max(value, 0.0)
        self.latency_samples += 1

    def snapshot(self) -> dict[str, object]:
        average_latency_ms = (
            self.latency_ms_total / self.latency_samples if self.latency_samples else 0.0
        )
        return {
            "counters": dict(sorted(self.counters.items())),
            "average_simulation_latency_ms": round(average_latency_ms, 6),
            "latency_samples": self.latency_samples,
        }

    def reset(self) -> None:
        self.counters.clear()
        self.latency_ms_total = 0.0
        self.latency_samples = 0


class LatencyTimer:
    def __init__(self, metrics: RuntimeMetrics) -> None:
        self._metrics = metrics
        self._started_at = 0.0

    def __enter__(self) -> LatencyTimer:
        self._started_at = perf_counter()
        return self

    def __exit__(self, *_: object) -> None:
        elapsed_ms = (perf_counter() - self._started_at) * 1_000.0
        self._metrics.observe_latency_ms(elapsed_ms)
