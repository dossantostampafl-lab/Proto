from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock


class LiveHistoryReadMetrics:
    """Process-local counters for the read-only persisted history surface."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests_total = 0
        self._successes_total = 0
        self._rows_returned_total = 0
        self._pages_with_more_total = 0
        self._cursor_rejections_total = 0
        self._backend_failures_total = 0
        self._disabled_total = 0
        self._last_success_at: datetime | None = None

    def record_request(self) -> None:
        with self._lock:
            self._requests_total += 1

    def record_success(self, *, rows_returned: int, has_more: bool) -> None:
        if (
            isinstance(rows_returned, bool)
            or not isinstance(rows_returned, int)
            or rows_returned < 0
        ):
            raise ValueError("rows_returned must be a non-negative integer")
        if not isinstance(has_more, bool):
            raise ValueError("has_more must be a boolean")
        with self._lock:
            self._successes_total += 1
            self._rows_returned_total += rows_returned
            if has_more:
                self._pages_with_more_total += 1
            self._last_success_at = datetime.now(UTC)

    def record_cursor_rejection(self) -> None:
        with self._lock:
            self._cursor_rejections_total += 1

    def record_backend_failure(self) -> None:
        with self._lock:
            self._backend_failures_total += 1

    def record_disabled(self) -> None:
        with self._lock:
            self._disabled_total += 1

    def reset(self) -> None:
        with self._lock:
            self._requests_total = 0
            self._successes_total = 0
            self._rows_returned_total = 0
            self._pages_with_more_total = 0
            self._cursor_rejections_total = 0
            self._backend_failures_total = 0
            self._disabled_total = 0
            self._last_success_at = None

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "requests_total": self._requests_total,
                "successes_total": self._successes_total,
                "rows_returned_total": self._rows_returned_total,
                "pages_with_more_total": self._pages_with_more_total,
                "cursor_rejections_total": self._cursor_rejections_total,
                "backend_failures_total": self._backend_failures_total,
                "disabled_total": self._disabled_total,
                "last_success_at": (
                    self._last_success_at.isoformat()
                    if self._last_success_at is not None
                    else None
                ),
                "financial_connectivity": False,
                "real_money_execution": False,
            }


live_history_read_metrics = LiveHistoryReadMetrics()
