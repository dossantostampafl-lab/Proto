from __future__ import annotations

from typing import Any

from sqlalchemy import select

from .store import JobRunRecord


async def list_creation_mission_runs(store: Any, mission_id: str) -> list[Any]:
    """Return durable job runs created for one The Creation mission.

    MissionGateway uses idempotency keys in the form
    ``mission:<mission_id>:<job_name>``. This query preserves that contract
    without introducing a second mission persistence model.
    """
    explicit = getattr(store, "list_for_mission", None)
    if explicit is not None:
        return list(await explicit(mission_id))

    session_factory = getattr(store, "session_factory", None)
    if session_factory is None:
        raise RuntimeError("PROTO orchestration store does not support Creation mission queries")

    prefix = f"mission:{mission_id}:"
    async with session_factory() as session:
        records = (
            await session.scalars(
                select(JobRunRecord)
                .where(JobRunRecord.idempotency_key.like(f"{prefix}%"))
                .order_by(JobRunRecord.created_at.asc())
            )
        ).all()
        return [record.as_run() for record in records]
