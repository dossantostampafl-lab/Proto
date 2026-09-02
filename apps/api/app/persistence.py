from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from services.orchestration import OrchestrationBase

from .models import Fill, SimulationOrder
from .portfolio import PaperPortfolio
from .portfolio_recovery import recover_paper_portfolio
from .schema_registry import canonical_metadata


class Base(DeclarativeBase):
    pass


class SimulationSessionRecord(Base):
    __tablename__ = "simulation_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    active: Mapped[bool] = mapped_column(Boolean, index=True)


class SimulationFillRecord(Base):
    __tablename__ = "simulation_fills"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "order_id",
            name="uq_simulation_fills_session_order",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), default="legacy", index=True)
    market_id: Mapped[str] = mapped_column(String(120), index=True)
    asset: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(8))
    filled_quantity: Mapped[float] = mapped_column(Float)
    fill_price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float)
    slippage_bps: Mapped[float] = mapped_column(Float)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    def as_dict(self) -> dict[str, object]:
        return {
            "order_id": self.order_id,
            "session_id": self.session_id,
            "market_id": self.market_id,
            "asset": self.asset,
            "side": self.side,
            "filled_quantity": self.filled_quantity,
            "fill_price": self.fill_price,
            "fee": self.fee,
            "slippage_bps": self.slippage_bps,
            "filled_at": self.filled_at.isoformat(),
        }


_recovery_target: PaperPortfolio | None = None


def register_portfolio_recovery_target(portfolio: PaperPortfolio) -> None:
    global _recovery_target
    _recovery_target = portfolio


def build_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


async def init_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(canonical_metadata.create_all)
        await connection.run_sync(OrchestrationBase.metadata.create_all)

    journal = AsyncSqlFillJournal(engine)
    await journal.ensure_active_session()
    if _recovery_target is not None:
        _recovery_target.reset()
        await recover_paper_portfolio(_recovery_target, journal.iter_chronological())


async def database_ready(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True


class AsyncSqlFillJournal:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def ensure_active_session(self) -> str:
        async with self.session_factory() as session:
            active_session = await session.scalar(
                select(SimulationSessionRecord.id)
                .where(SimulationSessionRecord.active.is_(True))
                .order_by(SimulationSessionRecord.created_at.desc())
                .limit(1)
            )
            if active_session is not None:
                return active_session

            existing_fill = await session.scalar(select(SimulationFillRecord.id).limit(1))
            session_id = "legacy" if existing_fill is not None else str(uuid4())
            session.add(
                SimulationSessionRecord(
                    id=session_id,
                    created_at=datetime.now(UTC),
                    active=True,
                )
            )
            await session.commit()
            return session_id

    async def start_new_session(self) -> str:
        session_id = str(uuid4())
        async with self.session_factory() as session:
            await session.execute(
                update(SimulationSessionRecord)
                .where(SimulationSessionRecord.active.is_(True))
                .values(active=False)
            )
            session.add(
                SimulationSessionRecord(
                    id=session_id,
                    created_at=datetime.now(UTC),
                    active=True,
                )
            )
            await session.commit()
        return session_id

    async def append(self, order: SimulationOrder, fill: Fill) -> bool:
        order_id = str(fill.order_id)
        session_id = await self.ensure_active_session()
        async with self.session_factory() as session:
            existing = await session.scalar(
                select(SimulationFillRecord.id).where(
                    SimulationFillRecord.session_id == session_id,
                    SimulationFillRecord.order_id == order_id,
                )
            )
            if existing is not None:
                return False

            session.add(
                SimulationFillRecord(
                    order_id=order_id,
                    session_id=session_id,
                    market_id=order.market_id,
                    asset=order.asset.value,
                    side=order.side.value,
                    filled_quantity=fill.filled_quantity,
                    fill_price=fill.fill_price,
                    fee=fill.fee,
                    slippage_bps=fill.slippage_bps,
                    filled_at=fill.filled_at,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False
            return True

    async def list(self, limit: int = 100) -> list[dict[str, object]]:
        safe_limit = min(max(limit, 1), 1_000)
        session_id = await self.ensure_active_session()
        async with self.session_factory() as session:
            result = await session.scalars(
                select(SimulationFillRecord)
                .where(SimulationFillRecord.session_id == session_id)
                .order_by(SimulationFillRecord.filled_at.desc(), SimulationFillRecord.id.desc())
                .limit(safe_limit)
            )
            return [record.as_dict() for record in result.all()]

    async def iter_chronological(self) -> AsyncIterator[dict[str, object]]:
        session_id = await self.ensure_active_session()
        async with self.session_factory() as session:
            stream = await session.stream_scalars(
                select(SimulationFillRecord)
                .where(SimulationFillRecord.session_id == session_id)
                .order_by(
                    SimulationFillRecord.filled_at.asc(),
                    SimulationFillRecord.id.asc(),
                )
            )
            async for record in stream:
                yield record.as_dict()
