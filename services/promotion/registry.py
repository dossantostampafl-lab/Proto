"""Durable promotion registry: one record per scope, saved after every change."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from services.execution import ReleaseStage

from .gates import (
    DEMOTION,
    STAGE_ORDER,
    Evidence,
    EvidenceKind,
    PromotionRefused,
    Scope,
    ScopeRecord,
    gate_check,
)

promotion_metadata = MetaData()

promotion_table = Table(
    "promotion_scopes",
    promotion_metadata,
    Column("scope_key", String(400), primary_key=True),
    Column("stage", String(16), nullable=False),
    Column("record", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _dump(record: ScopeRecord) -> str:
    return json.dumps(
        {
            "stage": record.stage.value,
            "stage_since": record.stage_since.isoformat(),
            "clean_since": record.clean_since.isoformat(),
            "evidence": [e.to_dict() for e in record.evidence],
            "violations": record.violations,
            "history": record.history,
        }
    )


def _load(scope: Scope, text: str) -> ScopeRecord:
    raw = json.loads(text)
    return ScopeRecord(
        scope=scope,
        stage=ReleaseStage(raw["stage"]),
        stage_since=datetime.fromisoformat(raw["stage_since"]),
        clean_since=datetime.fromisoformat(raw["clean_since"]),
        evidence=[
            Evidence(
                kind=EvidenceKind(e["kind"]),
                reference=e["reference"],
                passed=bool(e["passed"]),
                recorded_at=datetime.fromisoformat(e["recorded_at"]),
                recorded_by=e["recorded_by"],
            )
            for e in raw["evidence"]
        ],
        violations=list(raw["violations"]),
        history=list(raw["history"]),
    )


class PromotionRegistry:
    def __init__(
        self, engine: AsyncEngine | None = None, *, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._records: dict[str, ScopeRecord] = {}
        self._loaded = engine is None
        self._lock = asyncio.Lock()

    async def _ensure_loaded(self) -> None:
        if self._loaded or self._engine is None:
            return
        async with self._engine.begin() as connection:
            await connection.run_sync(promotion_metadata.create_all)
            rows = (await connection.execute(select(promotion_table))).mappings().all()
        for row in rows:
            scope = Scope.from_key(row["scope_key"])
            self._records[scope.key] = _load(scope, row["record"])
        self._loaded = True

    async def _save(self, record: ScopeRecord) -> None:
        if self._engine is None:
            return
        values = {
            "stage": record.stage.value,
            "record": _dump(record),
            "updated_at": self._clock(),
        }
        key = promotion_table.c.scope_key == record.scope.key
        async with self._engine.begin() as connection:
            result = await connection.execute(update(promotion_table).where(key).values(**values))
            if result.rowcount == 0:
                await connection.execute(
                    promotion_table.insert().values(scope_key=record.scope.key, **values)
                )

    def _record(self, scope: Scope) -> ScopeRecord:
        record = self._records.get(scope.key)
        if record is None:
            now = self._clock()
            record = ScopeRecord(scope, ReleaseStage.RESEARCH, now, now)
            self._records[scope.key] = record
        return record

    async def stage(self, scope: Scope) -> ReleaseStage:
        async with self._lock:
            await self._ensure_loaded()
            record = self._records.get(scope.key)
            return record.stage if record else ReleaseStage.RESEARCH

    async def records(self) -> list[ScopeRecord]:
        async with self._lock:
            await self._ensure_loaded()
            return sorted(self._records.values(), key=lambda r: r.scope.key)

    async def get(self, scope: Scope) -> ScopeRecord:
        async with self._lock:
            await self._ensure_loaded()
            return self._record(scope)

    async def add_evidence(
        self, scope: Scope, kind: EvidenceKind, *, reference: str, passed: bool, by: str
    ) -> ScopeRecord:
        async with self._lock:
            await self._ensure_loaded()
            record = self._record(scope)
            record.evidence.append(Evidence(kind, reference, passed, self._clock(), by))
            await self._save(record)
            return record

    async def promote(
        self, scope: Scope, target: ReleaseStage, *, operator: bool, by: str
    ) -> ScopeRecord:
        async with self._lock:
            await self._ensure_loaded()
            record = self._record(scope)
            now = self._clock()
            reasons = gate_check(record, target, now=now, operator=operator)
            if reasons:
                raise PromotionRefused(reasons)
            self._move(record, target, now, f"promoted by {by}")
            await self._save(record)
            return record

    async def demote(self, scope: Scope, *, reason: str, by: str) -> ScopeRecord:
        """Operator or automation: one stage down. Never refused."""

        async with self._lock:
            await self._ensure_loaded()
            record = self._record(scope)
            index = STAGE_ORDER.index(record.stage)
            if index > 0:
                self._move(record, STAGE_ORDER[index - 1], self._clock(), f"{reason} ({by})")
                await self._save(record)
            return record

    async def violation(self, *, venue: str, kind: str, detail: str) -> list[ScopeRecord]:
        """A safety violation on a venue: reset every matching clean period, demote live scopes."""

        async with self._lock:
            await self._ensure_loaded()
            now = self._clock()
            touched: list[ScopeRecord] = []
            for record in self._records.values():
                if record.scope.venue != venue:
                    continue
                record.clean_since = now
                record.violations.append({"at": now.isoformat(), "kind": kind, "detail": detail})
                target = DEMOTION.get(record.stage)
                if target is not None:
                    self._move(record, target, now, f"automatic demotion: {kind}")
                await self._save(record)
                touched.append(record)
            return touched

    @staticmethod
    def _move(record: ScopeRecord, target: ReleaseStage, now: datetime, why: str) -> None:
        record.history.append(
            {"at": now.isoformat(), "from": record.stage.value, "to": target.value, "why": why}
        )
        record.stage = target
        record.stage_since = now
