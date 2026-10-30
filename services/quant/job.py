"""Evidence-backed quant research handler for the existing durable ProtoBrain job.

No new scheduler, data source, order authority, or simulated defaults are introduced.
Only an already validated, explicitly sourced quant-evidence/1 envelope is accepted.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from services.orchestration.runtime import JobRun, ProtoBrain

from .evidence import QuantEvidence, _payload, store_quant_evidence

_ALLOWED_PROVENANCE = {
    "SIMULATION": "SIMULATED",
    "SHADOW": "PAPER",
    "HISTORICAL_REPLAY": "REPLAY",
}


async def process_quant_research_job(
    payload: dict[str, object],
    run: JobRun,
    engine: AsyncEngine | None,
) -> dict[str, object]:
    """Persist a reproducible quant result only when the durable job's mode matches.

    The mode and job name are taken from the store-backed JobRun, not from data supplied
    by the caller. store_quant_evidence re-runs the deterministic quant pipeline and
    rejects missing/corrupt provenance, invented results, and identity collisions.
    The ProtoBrain store owns retries, idempotency and the terminal job state.
    """
    if run.job_name != "quant-research" or run.mode not in _ALLOWED_PROVENANCE:
        raise ValueError("quant-research job identity or mode is invalid")
    if set(payload) != {"evidence"} or not isinstance(payload.get("evidence"), Mapping):
        raise ValueError("quant-research requires exactly one evidence envelope")
    envelope = QuantEvidence.model_validate(payload["evidence"])
    if envelope.mode != run.mode:
        raise ValueError("evidence mode differs from the queued job mode")
    if envelope.input_provenance != _ALLOWED_PROVENANCE[run.mode]:
        raise ValueError("input provenance does not match the queued research mode")
    if envelope.observed_at > datetime.now(UTC):
        raise ValueError("quant input cannot be observed in the future")

    # A successful job means that the complete validated envelope is durable.
    evidence_id = await store_quant_evidence(engine, envelope)
    return {
        "contract_version": envelope.contract_version,
        "evidence_id": evidence_id,
        "instrument_id": envelope.instrument_id,
        "mode": run.mode,
        "input_provenance": envelope.input_provenance,
        "model_version": envelope.model_version,
        "feature_version": envelope.feature_version,
        "result": dict(envelope.result),
        "persisted": True,
        "financial_connectivity": False,
        "real_money_execution": False,
    }


async def enqueue_quant_research(brain: ProtoBrain, envelope: QuantEvidence) -> JobRun:
    """Submit already sourced research to the existing durable queue exactly once.

    This is an internal integration seam, not an unauthenticated HTTP endpoint or
    a market-data producer. Callers must supply real, complete, timestamped evidence.
    """
    validated = QuantEvidence.model_validate(envelope.model_dump(mode="json"))
    if validated.mode not in _ALLOWED_PROVENANCE:
        raise ValueError("unsupported quant job mode")
    if validated.input_provenance != _ALLOWED_PROVENANCE[validated.mode]:
        raise ValueError("quant evidence mode/provenance mismatch")
    if validated.observed_at > datetime.now(UTC):
        raise ValueError("quant input cannot be observed in the future")
    evidence_id, payload = _payload(validated)
    return await brain.enqueue(
        "quant-research",
        idempotency_key=f"quant-evidence:{evidence_id}",
        mode=validated.mode,
        payload={"evidence": payload},
    )
