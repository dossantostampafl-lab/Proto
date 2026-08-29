from __future__ import annotations

from fastapi import APIRouter

from services.safety import evaluate_circuit_breakers

from .app_state import persistence_engine
from .event_state import event_runtime
from .live_monitor import live_monitor
from .models import SystemMode
from .persistence import database_ready
from .reconciliation_service import reconciliation_status
from .settings import settings

router = APIRouter(prefix="/safety", tags=["safety"])


@router.get("/circuit-breakers")
async def circuit_breaker_status() -> dict[str, object]:
    live_status = live_monitor.status()
    event_status = event_runtime.snapshot()

    if settings.persistence_enabled and persistence_engine is not None:
        database_available = await database_ready(persistence_engine)
    else:
        database_available = True

    reconciliation = await reconciliation_status()
    live_mode = settings.system_mode == SystemMode.LIVE_MONITORING.value
    data_fresh = bool(live_status["receiving_data"]) if live_mode else True

    decision = evaluate_circuit_breakers(
        data_fresh=data_fresh,
        database_available=database_available,
        event_bus_available=event_status.ready,
        risk_available=True,
        positions_consistent=bool(reconciliation["consistent"]),
        unknown_state=False,
    )

    return {
        "action": decision.action,
        "reasons": [reason.value for reason in decision.reasons],
        "halt_required": decision.halt_required,
        "live_monitoring": live_status,
        "event_runtime": {
            "backend": event_status.backend,
            "started": event_status.started,
            "ready": event_status.ready,
            "publish_count": event_status.publish_count,
            "publish_failures": event_status.publish_failures,
            "last_error": event_status.last_error,
        },
        "database_available": database_available,
        "positions_consistent": bool(reconciliation["consistent"]),
        "financial_connectivity": False,
        "real_money_execution": False,
    }
