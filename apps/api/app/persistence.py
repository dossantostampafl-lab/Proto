from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .models import Fill, SimulationOrder
from .schema_registry import canonical_metadata


class Base(DeclarativeBase):
    pass


class SimulationFillRecord(Base):
    __tablename__ = "simulation_fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
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
            "market_id": self.market_id,
            "asset": self.asset,
            "side": self.side,
            "filled_quantity": self.filled_quantity,
            "fill_price": self.fill_price,
            "fee": self.fee,
            "slippage_bps": self.slippage_bps,
            "filled_at": self.filled_at.isoformat(),
        }


def build_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


async def init_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(canonical_metadata.create_all)


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

    async def append(self, order: SimulationOrder, fill: Fill) -> None:
        order_id = str(fill.order_id)
        async with self.session_factory() as session:
            existing = await session.scalar(
                select(SimulationFillRecord.id).where(
                    SimulationFillRecord.order_id == order_id
                )
            )
            if existing is not None:
                return

            session.add(
                SimulationFillRecord(
                    order_id=order_id,
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
            await session.commit()

    async def list(self, limit: int = 100) -> list[dict[str, object]]:
        safe_limit = min(max(limit, 1), 1_000)
        async with self.session_factory() as session:
            result = await session.scalars(
                select(SimulationFillRecord)
                .order_by(SimulationFillRecord.filled_at.desc(), SimulationFillRecord.id.desc())
                .limit(safe_limit)
            )
            return [record.as_dict() for record in result.all()]

    async def iter_chronological(self) -> AsyncIterator[dict[str, object]]:
        async with self.session_factory() as session:
            stream = await session.stream_scalars(
                select(SimulationFillRecord).order_by(
                    SimulationFillRecord.filled_at.asc(),
                    SimulationFillRecord.id.asc(),
                )
            )
            async for record in stream:
                yield record.as_dict()
