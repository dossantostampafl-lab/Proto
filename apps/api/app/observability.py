from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from threading import Lock
from time import perf_counter


@dataclass
class RuntimeMetrics:
    counters: Counter[str] = field(default_factory=Counter)
    latency_ms_total: float = 0.0
    latency_samples: int = 0
    http_request_count: int = 0
    http_error_count: int = 0
    http_latency_ms_total: float = 0.0
    http_by_path: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    http_by_status: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    _lock: Lock = field(default_factory=Lock, repr=False)

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[name] += amount

    def observe_latency_ms(self, value: float) -> None:
        with self._lock:
            self.latency_ms_total += max(value, 0.0)
            self.latency_samples += 1

    def record_http(self, *, path: str, status_code: int, latency_ms: float) -> None:
        with self._lock:
            self.http_request_count += 1
            if status_code >= 500:
                self.http_error_count += 1
            self.http_latency_ms_total += max(latency_ms, 0.0)
            self.http_by_path[path] += 1
            self.http_by_status[status_code] += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            average_simulation_latency_ms = (
                self.latency_ms_total / self.latency_samples if self.latency_samples else 0.0
            )
            average_http_latency_ms = (
                self.http_latency_ms_total / self.http_request_count
                if self.http_request_count
                else 0.0
            )
            return {
                "counters": dict(sorted(self.counters.items())),
                "average_simulation_latency_ms": round(average_simulation_latency_ms, 6),
                "latency_samples": self.latency_samples,
                "http_request_count": self.http_request_count,
                "http_error_count": self.http_error_count,
                "average_http_latency_ms": round(average_http_latency_ms, 6),
                "http_by_path": dict(sorted(self.http_by_path.items())),
                "http_by_status": {
                    str(status): count for status, count in sorted(self.http_by_status.items())
                },
            }

    def reset(self) -> None:
        with self._lock:
            self.counters.clear()
            self.latency_ms_total = 0.0
            self.latency_samples = 0
            self.http_request_count = 0
            self.http_error_count = 0
            self.http_latency_ms_total = 0.0
            self.http_by_path.clear()
            self.http_by_status.clear()

    def prometheus(self) -> str:
        snapshot = self.snapshot()
        lines = [
            "# HELP proto_http_requests_total Total observed HTTP requests.",
            "# TYPE proto_http_requests_total counter",
            f"proto_http_requests_total {snapshot['http_request_count']}",
            "# HELP proto_http_errors_total Total observed HTTP 5xx responses.",
            "# TYPE proto_http_errors_total counter",
            f"proto_http_errors_total {snapshot['http_error_count']}",
            "# HELP proto_http_latency_milliseconds_average Average HTTP request latency.",
            "# TYPE proto_http_latency_milliseconds_average gauge",
            f"proto_http_latency_milliseconds_average {snapshot['average_http_latency_ms']}",
        ]
        counters = snapshot["counters"]
        if isinstance(counters, dict):
            for name, value in counters.items():
                safe_name = re.sub(r"[^a-zA-Z0-9_:]", "_", str(name))
                metric_name = f"proto_{safe_name}_total"
                lines.extend(
                    (
                        f"# TYPE {metric_name} counter",
                        f"{metric_name} {value}",
                    )
                )
        return "\n".join(lines) + "\n"


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


def access_log(
    *,
    request_id: str,
    method: str,
    path: str,
    status_code: int,
    latency_ms: float,
) -> str:
    return json.dumps(
        {
            "event": "http_request",
            "request_id": request_id,
            "method": method,
            "path": path,
            "status_code": status_code,
            "latency_ms": round(latency_ms, 6),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
