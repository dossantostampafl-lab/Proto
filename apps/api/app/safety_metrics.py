"""Safety metrics for Prometheus (Phase 7): watchdog, halts, lockdown, journal and promotion.

Read-only. Every value is the system's own state; nothing is estimated. `real_money_execution`
is exported as 0 so a dashboard or an alert can assert the financial boundary too.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from .live_metrics import _prometheus_label
from .models import KillSwitchState

router = APIRouter(prefix="/v1/safety", tags=["safety"])


def _gauge(lines: list[str], name: str, help_text: str, value: float, kind: str = "gauge") -> None:
    lines += [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}", f"{name} {value}"]


async def _promotion_lines() -> list[str]:
    """One series per promotion scope: stage index, clean days, violations, open blockers.

    The stage is the value, not a label, so a series stays the same across a promotion or a
    demotion and an alert can see the stage fall.
    """

    from services.promotion import STAGE_ORDER, gate_check, next_stage

    from .promotion_surface import registry

    now = datetime.now(UTC)
    records = await registry().records()
    series: dict[str, list[str]] = {
        "proto_promotion_stage": [],
        "proto_promotion_clean_days": [],
        "proto_promotion_violations": [],
        "proto_promotion_blockers": [],
    }
    for record in records:
        scope = record.scope
        labels = ",".join(
            f'{key}="{_prometheus_label(value)}"'
            for key, value in (
                ("account", scope.account),
                ("venue", scope.venue),
                ("strategy", scope.strategy_id),
                ("model", scope.model_version),
                ("credential", scope.credential_id),
            )
        )
        target = next_stage(record.stage)
        blockers = gate_check(record, target, now=now, operator=True) if target else []
        clean = record.clean_for(now).total_seconds() / 86_400
        series["proto_promotion_stage"].append(
            f"proto_promotion_stage{{{labels}}} {STAGE_ORDER.index(record.stage)}"
        )
        series["proto_promotion_clean_days"].append(
            f"proto_promotion_clean_days{{{labels}}} {clean:.3f}"
        )
        series["proto_promotion_violations"].append(
            f"proto_promotion_violations{{{labels}}} {len(record.violations)}"
        )
        series["proto_promotion_blockers"].append(
            f"proto_promotion_blockers{{{labels}}} {len(blockers)}"
        )
    help_text = {
        "proto_promotion_stage": "Promotion stage index (0 RESEARCH .. 5 LIVE) per scope.",
        "proto_promotion_clean_days": "Days in the current stage without a violation.",
        "proto_promotion_violations": "Violations recorded on the scope.",
        "proto_promotion_blockers": "Reasons the next stage is still refused (operator aside).",
    }
    lines: list[str] = []
    for name, rows in series.items():
        lines += [f"# HELP {name} {help_text[name]}", f"# TYPE {name} gauge", *rows]
    return lines


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
async def safety_prometheus() -> str:
    return render_safety_prometheus() + "\n".join(await _promotion_lines()) + "\n"
