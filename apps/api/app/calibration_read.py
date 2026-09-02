from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from .schema_registry import CANONICAL_TABLES


async def latest_calibration_metric(
    engine: AsyncEngine | None,
    *,
    model_version: str | None = None,
) -> dict[str, object] | None:
    """Return the newest persisted calibration payload matching a model version.

    Calibration metrics are append-only research lineage records. Reading them
    back through the canonical table keeps the API honest: no in-memory cache and
    no fabricated defaults are substituted when persistence has no evidence.
    """
    if engine is None:
        return None

    table = CANONICAL_TABLES["calibration_metrics"]
    async with engine.connect() as connection:
        result = await connection.execute(
            select(table.c.created_at, table.c.payload).order_by(
                table.c.created_at.desc(), table.c.id.desc()
            )
        )
        for created_at, payload in result:
            if not isinstance(payload, dict):
                continue
            if model_version is not None and payload.get("model_version") != model_version:
                continue
            return {
                **payload,
                "computed_at": created_at.isoformat(),
            }
    return None
