"""Safety metrics for Prometheus (Phase 7): watchdog, halts, lockdown and journal integrity.

Read-only. Every value is the system's own state; nothing is estimated. `real_money_execution`
is exported as 0 so a dashboard or an alert can assert the financial boundary too.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from .models import KillSwitchState

router = APIRouter(prefix="/v1/safety", tags=["safety"])


def _gauge(lines: list[str], name: str, help_text: str, value: float, kind: str = "gauge") -> None:
    lines += [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}", f"{name} {value}"]


def render_safety_prometheus() -> str:
    from .event_state import operational_journal
    from .risk_surface import governor, runtime
    from .watchdog_surface import state

    now = datetime.now(UTC)
    watch = state()
    controls = governor().snapshot()
    lines: list[str] = []
    _gauge(
        lines,
        "proto_watchdog_active",
        "Whether the watchdog heartbeat is fresh.",
        int(watch.active(now)),
    )
    _gauge(
        lines,
        "proto_watchdog_coverage_seconds",
        "Continuous watchdog coverage without a heartbeat gap.",
        round(watch.coverage(now).total_seconds(), 1),
    )
    _gauge(
        lines,
        "proto_watchdog_trips_total",
        "Watchdog halts recorded since the API started.",
        len(watch.trips),
        "counter",
    )
    _gauge(
        lines,
        "proto_global_kill_switch",
        "Whether the Risk Governor's global kill switch is on.",
        int(bool(controls["global_kill"])),
    )
    _gauge(
        lines,
        "proto_runtime_kill_switch",
        "Whether the runtime kill switch has left ARMED.",
        int(runtime.kill_switch != KillSwitchState.ARMED),
    )
    _gauge(
        lines,
        "proto_killed_venues",
        "Venues halted by a kill switch.",
        len(controls["killed_venues"]),
    )
    _gauge(
        lines,
        "proto_lockdown_active",
        "Whether a daily-loss or drawdown lockdown is latched.",
        int(controls["lockdown"] is not None),
    )
    _gauge(
        lines,
        "proto_journal_valid",
        "Whether the operational journal verifies.",
        int(operational_journal.integrity.value == "VALID"),
    )
    _gauge(lines, "proto_real_money_execution", "Real-money execution is not enabled anywhere.", 0)
    return "\n".join(lines) + "\n"


@router.get("/metrics/prometheus", response_class=PlainTextResponse)
def safety_prometheus() -> str:
    return render_safety_prometheus()
