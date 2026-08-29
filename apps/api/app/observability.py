from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class RuntimeMetrics:
    request_count: int = 0
    error_count: int = 0
    total_latency_ms: float = 0.0
    by_path: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_status: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    _lock: Lock = field(default_factory=Lock, repr=False)

    def record(self, *, path: str, status_code: int, latency_ms: float) -> None:
        with self._lock:
            self.request_count += 1
            if status_code >= 500:
                self.error_count += 1
            self.total_latency_ms += latency_ms
            self.by_path[path] += 1
            self.by_status[status_code] += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            average_latency_ms = (
                self.total_latency_ms / self.request_count if self.request_count else 0.0
            )
            return {
                "request_count": self.request_count,
                "error_count": self.error_count,
                "average_latency_ms": round(average_latency_ms, 6),
                "by_path": dict(sorted(self.by_path.items())),
                "by_status": {
                    str(status): count for status, count in sorted(self.by_status.items())
                },
            }


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
