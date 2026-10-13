from __future__ import annotations

from fastapi import APIRouter

from services.events.reconciliation import ReconciliationIssue, ReconciliationResult
from services.events.reconciliation_guard import assess_reconciliation

from .app_state import runtime
from .metrics_state import metrics
from .models import KillSwitchState
from .reconciliation_service import reconciliation_status
from .risk_state import risk_snapshot
from .runtime_control import broadcast_runtime
from .websockets import hub

router = APIRouter(tags=["safety"])


@router.post("/v1/reconciliation/enforce")
async def enforce_reconciliation() -> dict[str, object]:
    status = await reconciliation_status()
    result = ReconciliationResult(
        consistent=bool(status["consistent"]),
        issues=tuple(ReconciliationIssue(issue) for issue in status["issues"]),
    )
    decision = assess_reconciliation(result)
    if decision.halt_required:
        runtime.running = False
        runtime.kill_switch = KillSwitchState.TRIGGERED
        metrics.increment("reconciliation_halts")
        await hub.broadcast(
            "risk",
            {"type": "risk", "data": risk_snapshot()},
        )
        await broadcast_runtime()

    return {
        **status,
        "action": decision.action,
        "halted": decision.halt_required,
        "kill_switch": runtime.kill_switch,
    }
