from __future__ import annotations

from fastapi import APIRouter

from services.events.reconciliation import ReconciliationIssue, ReconciliationResult
from services.events.reconciliation_guard import assess_reconciliation

router = APIRouter(tags=["safety"])


@router.post("/v1/reconciliation/enforce")
async def enforce_reconciliation() -> dict[str, object]:
    from . import main as api_main

    status = await api_main.reconciliation_status()
    result = ReconciliationResult(
        consistent=bool(status["consistent"]),
        issues=tuple(ReconciliationIssue(issue) for issue in status["issues"]),
    )
    decision = assess_reconciliation(result)
    if decision.halt_required:
        api_main.runtime.running = False
        api_main.runtime.kill_switch = api_main.KillSwitchState.TRIGGERED
        api_main.metrics.increment("reconciliation_halts")
        await api_main.hub.broadcast(
            "risk",
            {"type": "risk", "data": api_main.risk()},
        )
        await api_main.hub.broadcast(
            "analytics",
            {
                "type": "runtime",
                "data": api_main.runtime.model_dump(mode="json"),
            },
        )

    return {
        **status,
        "action": decision.action,
        "halted": decision.halt_required,
        "kill_switch": api_main.runtime.kill_switch,
    }
