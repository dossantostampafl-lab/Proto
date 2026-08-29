from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .models import Fill, SimulationOrder


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


def build_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def init_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)


class SqlFillJournal:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def append(self, order: SimulationOrder, fill: Fill) -> None:
        order_id = str(fill.order_id)
        with Session(self.engine) as session:
            existing = session.scalar(
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
            session.commit()

    def list(self, limit: int = 100) -> list[dict[str, object]]:
        safe_limit = min(max(limit, 1), 1_000)
        with Session(self.engine) as session:
            records = session.scalars(
                select(SimulationFillRecord)
                .order_by(SimulationFillRecord.filled_at.desc(), SimulationFillRecord.id.desc())
                .limit(safe_limit)
            ).all()
            return [record.as_dict() for record in records]
