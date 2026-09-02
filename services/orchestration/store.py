from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .runtime import JobCapability, JobRun, JobSpec, JobState, ProtoBrain


class OrchestrationBase(DeclarativeBase):
    pass


class JobRunRecord(OrchestrationBase):
    __tablename__ = "orchestration_job_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_orchestration_job_runs_idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), index=True)
    job_name: Mapped[str] = mapped_column(String(120), index=True)
    capability: Mapped[str] = mapped_column(String(40), index=True)
    mode: Mapped[str] = mapped_column(String(32), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    def as_run(self) -> JobRun:
        return JobRun(
            id=self.id,
            idempotency_key=self.idempotency_key,
            job_name=self.job_name,
            capability=JobCapability(self.capability),
            mode=self.mode,
            payload=json.loads(self.payload_json),
            state=JobState(self.state),
            attempts=self.attempts,
            max_attempts=self.max_attempts,
            created_at=_utc(self.created_at),
            updated_at=_utc(self.updated_at),
            not_before=_utc(self.not_before),
            lease_owner=self.lease_owner,
            lease_expires_at=_utc_optional(self.lease_expires_at),
            heartbeat_at=_utc_optional(self.heartbeat_at),
            last_error=self.last_error,
            result=json.loads(self.result_json) if self.result_json else None,
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_optional(value: datetime | None) -> datetime | None:
    return _utc(value) if value is not None else None


class SqlJobStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def init_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(OrchestrationBase.metadata.create_all)

    async def enqueue(
        self,
        spec: JobSpec,
        *,
        idempotency_key: str,
        mode: str,
        payload: dict[str, object],
    ) -> JobRun:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            existing = await session.scalar(
                select(JobRunRecord).where(JobRunRecord.idempotency_key == idempotency_key)
            )
            if existing is not None:
                if existing.job_name != spec.name or existing.mode != mode:
                    raise ValueError("idempotency key already belongs to a different job contract")
                return existing.as_run()

            record = JobRunRecord(
                id=str(uuid4()),
                idempotency_key=idempotency_key,
                job_name=spec.name,
                capability=spec.capability.value,
                mode=mode,
                payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
                state=JobState.QUEUED.value,
                attempts=0,
                max_attempts=spec.max_attempts,
                created_at=now,
                updated_at=now,
                not_before=now,
            )
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(JobRunRecord).where(JobRunRecord.idempotency_key == idempotency_key)
                )
                if existing is None:
                    raise
                return existing.as_run()
            await session.refresh(record)
            return record.as_run()

    async def claim_next(
        self,
        specs: dict[str, JobSpec],
        owner: str,
        *,
        now: datetime,
    ) -> JobRun | None:
        names = tuple(specs)
        if not names:
            return None
        async with self.session_factory() as session:
            record = await session.scalar(
                select(JobRunRecord)
                .where(
                    JobRunRecord.job_name.in_(names),
                    JobRunRecord.state.in_((JobState.QUEUED.value, JobState.RETRY_WAIT.value)),
                    JobRunRecord.not_before <= now,
                )
                .order_by(JobRunRecord.not_before.asc(), JobRunRecord.created_at.asc())
                .limit(1)
            )
            if record is None:
                return None

            spec = specs[record.job_name]
            result = await session.execute(
                update(JobRunRecord)
                .where(
                    JobRunRecord.id == record.id,
                    JobRunRecord.state.in_((JobState.QUEUED.value, JobState.RETRY_WAIT.value)),
                    JobRunRecord.not_before <= now,
                )
                .values(
                    state=JobState.RUNNING.value,
                    attempts=JobRunRecord.attempts + 1,
                    lease_owner=owner,
                    lease_expires_at=now + __import__("datetime").timedelta(seconds=spec.lease_seconds),
                    heartbeat_at=now,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                return None
            await session.commit()
            claimed = await session.get(JobRunRecord, record.id)
            return claimed.as_run() if claimed is not None else None

    async def heartbeat(self, run_id: str, *, owner: str, now: datetime) -> JobRun:
        async with self.session_factory() as session:
            record = await self._owned_running(session, run_id, owner)
            await session.execute(
                update(JobRunRecord)
                .where(JobRunRecord.id == run_id)
                .values(heartbeat_at=now, updated_at=now)
            )
            await session.commit()
            await session.refresh(record)
            return record.as_run()

    async def succeed(
        self,
        run_id: str,
        *,
        owner: str,
        result: dict[str, object],
        now: datetime,
    ) -> JobRun:
        return await self._terminal_transition(
            run_id,
            owner=owner,
            state=JobState.SUCCEEDED,
            now=now,
            result=result,
        )

    async def block(self, run_id: str, *, owner: str, error: str, now: datetime) -> JobRun:
        return await self._terminal_transition(
            run_id,
            owner=owner,
            state=JobState.BLOCKED,
            now=now,
            error=error,
        )

    async def dead_letter(self, run_id: str, *, owner: str, error: str, now: datetime) -> JobRun:
        return await self._terminal_transition(
            run_id,
            owner=owner,
            state=JobState.DEAD_LETTER,
            now=now,
            error=error,
        )

    async def fail(
        self,
        run_id: str,
        *,
        owner: str,
        spec: JobSpec,
        error: str,
        now: datetime,
    ) -> JobRun:
        async with self.session_factory() as session:
            record = await self._owned_running(session, run_id, owner)
            state = JobState.DEAD_LETTER if record.attempts >= spec.max_attempts else JobState.RETRY_WAIT
            not_before = (
                now
                if state is JobState.DEAD_LETTER
                else ProtoBrain.next_retry_at(spec, record.attempts, now)
            )
            await session.execute(
                update(JobRunRecord)
                .where(JobRunRecord.id == run_id)
                .values(
                    state=state.value,
                    not_before=not_before,
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    last_error=error,
                    updated_at=now,
                )
            )
            await session.commit()
            updated_record = await session.get(JobRunRecord, run_id)
            if updated_record is None:
                raise RuntimeError("job disappeared after failure transition")
            return updated_record.as_run()

    async def recover_stale(self, specs: dict[str, JobSpec], *, now: datetime) -> int:
        async with self.session_factory() as session:
            stale = list(
                (
                    await session.scalars(
                        select(JobRunRecord).where(
                            JobRunRecord.state == JobState.RUNNING.value,
                            JobRunRecord.lease_expires_at.is_not(None),
                            JobRunRecord.lease_expires_at <= now,
                        )
                    )
                ).all()
            )
            recovered = 0
            for record in stale:
                spec = specs.get(record.job_name)
                if spec is None or record.attempts >= record.max_attempts:
                    state = JobState.DEAD_LETTER
                    not_before = now
                else:
                    state = JobState.RETRY_WAIT
                    not_before = ProtoBrain.next_retry_at(spec, record.attempts, now)
                record.state = state.value
                record.not_before = not_before
                record.lease_owner = None
                record.lease_expires_at = None
                record.heartbeat_at = None
                record.last_error = "worker lease expired"
                record.updated_at = now
                recovered += 1
            if recovered:
                await session.commit()
            return recovered

    async def get(self, run_id: str) -> JobRun | None:
        async with self.session_factory() as session:
            record = await session.get(JobRunRecord, run_id)
            return record.as_run() if record is not None else None

    async def list_dead_letters(self, limit: int = 100) -> list[JobRun]:
        safe_limit = min(max(limit, 1), 1_000)
        async with self.session_factory() as session:
            records = (
                await session.scalars(
                    select(JobRunRecord)
                    .where(JobRunRecord.state == JobState.DEAD_LETTER.value)
                    .order_by(JobRunRecord.updated_at.desc())
                    .limit(safe_limit)
                )
            ).all()
            return [record.as_run() for record in records]

    async def _owned_running(self, session: object, run_id: str, owner: str) -> JobRunRecord:
        record = await session.scalar(
            select(JobRunRecord).where(
                and_(
                    JobRunRecord.id == run_id,
                    JobRunRecord.state == JobState.RUNNING.value,
                    JobRunRecord.lease_owner == owner,
                )
            )
        )
        if record is None:
            raise RuntimeError("job is not owned by this worker")
        return record

    async def _terminal_transition(
        self,
        run_id: str,
        *,
        owner: str,
        state: JobState,
        now: datetime,
        error: str | None = None,
        result: dict[str, object] | None = None,
    ) -> JobRun:
        async with self.session_factory() as session:
            await self._owned_running(session, run_id, owner)
            await session.execute(
                update(JobRunRecord)
                .where(JobRunRecord.id == run_id)
                .values(
                    state=state.value,
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    last_error=error,
                    result_json=(
                        json.dumps(result, sort_keys=True, separators=(",", ":"))
                        if result is not None
                        else None
                    ),
                    updated_at=now,
                )
            )
            await session.commit()
            record = await session.get(JobRunRecord, run_id)
            if record is None:
                raise RuntimeError("job disappeared after terminal transition")
            return record.as_run()
