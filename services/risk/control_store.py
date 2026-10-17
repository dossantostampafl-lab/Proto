"""Durable control-plane records: kill switches, the lockdown latch, watchdog coverage.

One JSON document per key. The governor and the watchdog are synchronous and in-memory; the
API persists their state here after every change and restores it on start, so a deploy can
neither clear a lockdown nor release a kill switch. Restoring only ever makes state stricter
(``RiskGovernor.restore``). Migration ``0013_control_state``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

control_metadata = MetaData()

control_state_table = Table(
    "control_state",
    control_metadata,
    Column("state_key", String(80), primary_key=True),
    Column("document", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


class SqlControlState:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create_schema(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(control_metadata.create_all)

    async def get(self, key: str) -> dict[str, Any] | None:
        table = control_state_table
        async with self._engine.connect() as connection:
            row = (
                await connection.execute(select(table.c.document).where(table.c.state_key == key))
            ).first()
        return None if row is None else json.loads(row[0])

    async def put(self, key: str, document: Mapping[str, Any]) -> None:
        table = control_state_table
        values = {
            "document": json.dumps(document, sort_keys=True),
            "updated_at": datetime.now(UTC),
        }
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(table).where(table.c.state_key == key).values(**values)
            )
            if result.rowcount == 0:
                await connection.execute(table.insert().values(state_key=key, **values))
