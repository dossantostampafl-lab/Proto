from __future__ import annotations

from services.orchestration import JOB_CATALOG, ProtoBrain
from services.orchestration.supervisor import OrchestrationSupervisor, PeriodicJob

from .app_state import orchestration_store
from .live_monitor import live_monitor


async def _market_data_health_job(_: dict[str, object]) -> dict[str, object]:
    status = live_monitor.status()
    return {
        "mode": str(status["mode"]),
        "running": bool(status["running"]),
        "receiving_data": bool(status.get("receiving_data", False)),
        "complete": bool(status.get("complete", False)),
        "all_symbols_fresh": bool(status.get("all_symbols_fresh", False)),
        "source_message_fresh": bool(status.get("source_message_fresh", False)),
        "provider": status.get("provider"),
        "source": status.get("source"),
        "symbols": status.get("symbols", []),
        "financial_connectivity": False,
        "real_money_execution": False,
    }


proto_brain: ProtoBrain | None = None
orchestration_supervisor: OrchestrationSupervisor | None = None

if orchestration_store is not None:
    proto_brain = ProtoBrain(store=orchestration_store, worker_id="railway-api-safe-worker")
    proto_brain.register(JOB_CATALOG["market-data-health"].spec, _market_data_health_job)
    orchestration_supervisor = OrchestrationSupervisor(
        proto_brain,
        (
            PeriodicJob(
                job_name="market-data-health",
                interval_seconds=10,
                mode="LIVE_MONITORING",
            ),
        ),
        poll_seconds=1.0,
    )
