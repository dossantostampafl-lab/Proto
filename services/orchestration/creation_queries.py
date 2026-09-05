from __future__ import annotations

from typing import Any

from sqlalchemy import select

from .store import JobRunRecord


async def list_creation_mission_runs(store: Any, mission_id: str) -> list[Any]:
    """Return durable job runs created for one The Creation mission."""
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


async def recent_creation_runs(store: Any, limit: int = 50) -> list[Any]:
    """Return recent durable runs submitted through the Creation bridge."""
    safe_limit = min(max(limit, 1), 200)
    explicit = getattr(store, "recent_creation_runs", None)
    if explicit is not None:
        return list(await explicit(safe_limit))

    session_factory = getattr(store, "session_factory", None)
    if session_factory is None:
        raise RuntimeError("PROTO orchestration store does not support Creation activity queries")

    async with session_factory() as session:
        records = (
            await session.scalars(
                select(JobRunRecord)
                .where(JobRunRecord.idempotency_key.like("mission:%"))
                .order_by(JobRunRecord.updated_at.desc())
                .limit(safe_limit)
            )
        ).all()
        return [record.as_run() for record in records]
