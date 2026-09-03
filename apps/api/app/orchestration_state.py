from __future__ import annotations

from datetime import UTC, datetime

from services.analytics.intelligence import (
    MarketIntelligenceInput,
    OpportunityPolicy,
    RegimePolicy,
    classify_market_state,
    rank_opportunities,
)
from services.orchestration import (
    JOB_CATALOG,
    DecisionMemoryEntry,
    DecisionStage,
    ProtoBrain,
)
from services.orchestration.supervisor import OrchestrationSupervisor, PeriodicJob

from .app_state import decision_memory_store, orchestration_store
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


async def _opportunity_scan_job(payload: dict[str, object]) -> dict[str, object]:
    observations_raw = payload.get("observations")
    regime_policy_raw = payload.get("regime_policy")
    opportunity_policy_raw = payload.get("opportunity_policy")
    limit_raw = payload.get("limit")

    if not isinstance(observations_raw, list) or not observations_raw:
        raise ValueError("opportunity scan requires a non-empty observations list")
    if not isinstance(regime_policy_raw, dict):
        raise ValueError("opportunity scan requires an explicit regime_policy")
    if not isinstance(opportunity_policy_raw, dict):
        raise ValueError("opportunity scan requires an explicit opportunity_policy")
    if isinstance(limit_raw, bool) or not isinstance(limit_raw, int) or limit_raw < 1:
        raise ValueError("opportunity scan requires an integer limit >= 1")

    observations = [MarketIntelligenceInput.model_validate(item) for item in observations_raw]
    regime_policy = RegimePolicy.model_validate(regime_policy_raw)
    opportunity_policy = OpportunityPolicy.model_validate(opportunity_policy_raw)
    states = [classify_market_state(item, regime_policy) for item in observations]
    ranked = rank_opportunities(states, opportunity_policy, limit=limit_raw)

    return {
        "observation_count": len(observations),
        "classified_count": len(states),
        "opportunity_count": len(ranked),
        "states": [state.model_dump(mode="json") for state in states],
        "opportunities": [item.model_dump(mode="json") for item in ranked],
        "policy": {
            "regime": regime_policy.model_dump(mode="json"),
            "opportunity": opportunity_policy.model_dump(mode="json"),
            "limit": limit_raw,
        },
        "incomplete_evidence_policy": "OMIT_FROM_RANKING",
        "financial_connectivity": False,
        "real_money_execution": False,
    }


async def _shadow_decision_job(payload: dict[str, object]) -> dict[str, object]:
    if decision_memory_store is None:
        raise RuntimeError("decision memory persistence is unavailable")

    required = (
        "decision_id",
        "instrument_id",
        "observed_at",
        "input_hash",
        "proposed_action",
        "provenance",
    )
    missing = [name for name in required if name not in payload]
    if missing:
        raise ValueError("shadow decision missing required fields: " + ",".join(missing))

    entry = DecisionMemoryEntry.model_validate(
        {
            "decision_id": payload["decision_id"],
            "mission_id": payload.get("mission_id"),
            "instrument_id": payload["instrument_id"],
            "observed_at": payload["observed_at"],
            "recorded_at": datetime.now(UTC),
            "stage": DecisionStage.SHADOW_ONLY,
            "model_id": payload.get("model_id"),
            "model_version": payload.get("model_version"),
            "feature_version": payload.get("feature_version"),
            "calibration_version": payload.get("calibration_version"),
            "input_hash": payload["input_hash"],
            "regime": payload.get("regime"),
            "probability": payload.get("probability"),
            "uncertainty": payload.get("uncertainty"),
            "edge": payload.get("edge"),
            "risk_decision": payload.get("risk_decision"),
            "proposed_action": payload["proposed_action"],
            "actual_action": None,
            "explanation": payload.get("explanation"),
            "provenance": payload["provenance"],
        }
    )
    stored = await decision_memory_store.record(entry)
    return {
        "decision": stored.model_dump(mode="json"),
        "stage": DecisionStage.SHADOW_ONLY.value,
        "executed": False,
        "memory_persisted": True,
        "financial_connectivity": False,
        "real_money_execution": False,
    }


proto_brain: ProtoBrain | None = None
orchestration_supervisor: OrchestrationSupervisor | None = None

if orchestration_store is not None:
    proto_brain = ProtoBrain(store=orchestration_store, worker_id="railway-api-safe-worker")
    proto_brain.register(JOB_CATALOG["market-data-health"].spec, _market_data_health_job)
    proto_brain.register(JOB_CATALOG["opportunity-scan"].spec, _opportunity_scan_job)
    proto_brain.register(JOB_CATALOG["shadow-decision"].spec, _shadow_decision_job)
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
