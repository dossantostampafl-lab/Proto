"""Durable single-use risk-permit consumption, for the canonical OMS.

An INSERT guarded by database unique constraints is the authority. This
prevents separate workers and process restarts from reusing a RiskPermit.
It does not issue permits, route orders, or enable real-money execution.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, MetaData, String, Table, UniqueConstraint, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from services.execution import ReleaseStage, RiskPermit, TradeIntent

from .permits import PermitRejected, PermitSigner

permit_metadata = MetaData()

consumed_permits = Table(
    "execution_consumed_permits",
    permit_metadata,
    Column("permit_id", String(36), primary_key=True),
    Column("intent_id", String(36), nullable=False),
    Column("intent_fingerprint", String(64), nullable=False),
    Column("client_order_id", String(36), nullable=False),
    Column("consumed_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("client_order_id", name="uq_execution_permit_client_order"),
)


class DurablePermitLedger:
    """Fail-closed transactional gate shared by all OMS workers.

    The database must be migrated before use. Missing or unavailable storage
    raises and never falls back to an in-memory ledger.
    """

    def __init__(self, engine: AsyncEngine, signer: PermitSigner) -> None:
        self._engine = engine
        self._signer = signer

    async def consume_in_transaction(
        self,
        connection: AsyncConnection,
        permit: RiskPermit,
        intent: TradeIntent,
        *,
        now: datetime,
    ) -> None:
        """Use the caller's transaction so a failed order INSERT rolls back its permit."""

        self._signer.verify(permit, intent, now=now)
        if intent.stage not in {ReleaseStage.PAPER, ReleaseStage.SHADOW}:
            raise PermitRejected("stage_not_certified")
        await connection.execute(
            insert(consumed_permits).values(
                permit_id=str(permit.permit_id),
                intent_id=str(intent.intent_id),
                intent_fingerprint=intent.fingerprint(),
                client_order_id=intent.client_order_id(0),
                consumed_at=now,
            )
        )

    async def consume(
        self, permit: RiskPermit, intent: TradeIntent, *, now: datetime
    ) -> None:
        permit_id = str(permit.permit_id)
        order_id = intent.client_order_id(0)
        try:
            async with self._engine.begin() as connection:
                await self.consume_in_transaction(connection, permit, intent, now=now)
        except IntegrityError as exc:
            # Only a verified duplicate is mapped to a domain rejection.
            # Database/operational failures always propagate (fail closed).
            async with self._engine.connect() as connection:
                exists = await connection.scalar(
                    select(consumed_permits.c.permit_id).where(
                        consumed_permits.c.permit_id == permit_id
                    )
                )
                if exists is not None:
                    raise PermitRejected("already_used") from exc
                order_exists = await connection.scalar(
                    select(consumed_permits.c.permit_id).where(
                        consumed_permits.c.client_order_id == order_id
                    )
                )
                if order_exists is not None:
                    raise PermitRejected("order_already_authorized") from exc
            raise
