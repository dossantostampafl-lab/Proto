from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime, timedelta
from math import isfinite

from sqlalchemy import (
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
    and_,
    delete,
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from services.market_data import (
    LiveHistoryCursorError,
    LiveTickJournalError,
    MarketTick,
    PersistedLiveTick,
    PersistedLiveTickPage,
)

from .live_database import LiveBase

_HISTORY_CURSOR_VERSION = 2
_MAX_HISTORY_CURSOR_CHARS = 512


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _require_aware(value: datetime | None, *, field: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _cursor_bound(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None


def _encode_history_cursor(
    *,
    symbol: str,
    received_at: datetime,
    row_id: int,
    start_at: datetime | None,
    end_at: datetime | None,
) -> str:
    payload = json.dumps(
        {
            "v": _HISTORY_CURSOR_VERSION,
            "symbol": symbol,
            "received_at": _as_utc(received_at).isoformat(),
            "id": row_id,
            "start_at": _cursor_bound(start_at),
            "end_at": _cursor_bound(end_at),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_history_cursor(
    cursor: str,
    *,
    symbol: str,
    start_at: datetime | None,
    end_at: datetime | None,
) -> tuple[datetime, int]:
    if not cursor or len(cursor) > _MAX_HISTORY_CURSOR_CHARS:
        raise LiveHistoryCursorError("history cursor is malformed")
    try:
        encoded = cursor.encode("ascii")
        padded = encoded + b"=" * (-len(encoded) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError, binascii.Error, json.JSONDecodeError) as error:
        raise LiveHistoryCursorError("history cursor is malformed") from error

    if not isinstance(payload, dict) or payload.get("v") != _HISTORY_CURSOR_VERSION:
        raise LiveHistoryCursorError("history cursor version is unsupported")
    if payload.get("symbol") != symbol:
        raise LiveHistoryCursorError("history cursor does not match symbol")
    if payload.get("start_at") != _cursor_bound(start_at):
        raise LiveHistoryCursorError("history cursor does not match start_at")
    if payload.get("end_at") != _cursor_bound(end_at):
        raise LiveHistoryCursorError("history cursor does not match end_at")

    row_id = payload.get("id")
    received_raw = payload.get("received_at")
    if isinstance(row_id, bool) or not isinstance(row_id, int) or row_id <= 0:
        raise LiveHistoryCursorError("history cursor row id is invalid")
    if not isinstance(received_raw, str):
        raise LiveHistoryCursorError("history cursor timestamp is invalid")
    try:
        received_at = datetime.fromisoformat(received_raw)
    except ValueError as error:
        raise LiveHistoryCursorError("history cursor timestamp is invalid") from error
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise LiveHistoryCursorError("history cursor timestamp must be timezone-aware")
    return received_at.astimezone(UTC), row_id


class LiveMarketTickRecord(LiveBase):
    __tablename__ = "live_market_ticks"
    __table_args__ = (
        UniqueConstraint(
            "venue",
            "symbol",
            "sequence",
            name="uq_live_market_ticks_source_sequence",
        ),
        Index("ix_live_market_ticks_symbol_received", "symbol", "received_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venue: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    connection_generation: Mapped[int] = mapped_column(Integer, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    source_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    persisted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    bid: Mapped[float] = mapped_column(Float)
    ask: Mapped[float] = mapped_column(Float)
    last: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    bid_size: Mapped[float] = mapped_column(Float)
    ask_size: Mapped[float] = mapped_column(Float)

    def as_persisted(self) -> PersistedLiveTick:
        return PersistedLiveTick(
            tick=MarketTick(
                timestamp=_as_utc(self.source_at),
                venue=self.venue,
                symbol=self.symbol,
                bid=self.bid,
                ask=self.ask,
                last=self.last,
                volume=self.volume,
                bid_size=self.bid_size,
                ask_size=self.ask_size,
                sequence=self.sequence,
            ),
            received_at=_as_utc(self.received_at),
            connection_generation=self.connection_generation,
            persisted_at=_as_utc(self.persisted_at),
        )


class AsyncSqlLiveTickJournal:
    """Idempotent SQL journal for accepted public read-only market ticks."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        retention_seconds: int = 86_400,
        prune_every_writes: int = 1_000,
    ) -> None:
        if (
            isinstance(retention_seconds, bool)
            or not isinstance(retention_seconds, int)
            or retention_seconds <= 0
        ):
            raise ValueError("retention_seconds must be a positive integer")
        if (
            isinstance(prune_every_writes, bool)
            or not isinstance(prune_every_writes, int)
            or prune_every_writes <= 0
        ):
            raise ValueError("prune_every_writes must be a positive integer")
        self.engine = engine
        self.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self.retention_seconds = retention_seconds
        self.prune_every_writes = prune_every_writes
        self._writes_attempted = 0
        self._writes_inserted = 0
        self._idempotent_hits = 0
        self._write_failures = 0
        self._read_failures = 0
        self._maintenance_failures = 0
        self._pruned_rows = 0
        self._writes_since_prune = 0
        self._last_write_error: str | None = None
        self._last_read_error: str | None = None
        self._last_maintenance_error: str | None = None
        self._last_write_success_at: datetime | None = None
        self._last_prune_at: datetime | None = None

    async def append(
        self,
        tick: MarketTick,
        *,
        received_at: datetime,
        connection_generation: int,
    ) -> bool:
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        if (
            isinstance(connection_generation, bool)
            or not isinstance(connection_generation, int)
            or connection_generation < 0
        ):
            raise ValueError("connection_generation must be a non-negative integer")
        numeric_values = (
            tick.bid,
            tick.ask,
            tick.last,
            tick.volume,
            tick.bid_size,
            tick.ask_size,
        )
        if not all(isfinite(value) for value in numeric_values):
            raise ValueError("live persistence refuses non-finite market values")

        self._writes_attempted += 1
        persisted_at = datetime.now(UTC)
        async with self.session_factory() as session:
            session.add(
                LiveMarketTickRecord(
                    venue=tick.venue,
                    symbol=tick.symbol,
                    connection_generation=connection_generation,
                    sequence=tick.sequence,
                    source_at=tick.timestamp,
                    received_at=received_at,
                    persisted_at=persisted_at,
                    bid=tick.bid,
                    ask=tick.ask,
                    last=tick.last,
                    volume=tick.volume,
                    bid_size=tick.bid_size,
                    ask_size=tick.ask_size,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                self._idempotent_hits += 1
                self._last_write_error = None
                return False
            except SQLAlchemyError as error:
                await session.rollback()
                self._write_failures += 1
                self._last_write_error = type(error).__name__
                raise LiveTickJournalError("failed to persist live market tick") from error

        self._writes_inserted += 1
        self._writes_since_prune += 1
        self._last_write_error = None
        self._last_write_success_at = persisted_at
        if self._writes_since_prune >= self.prune_every_writes:
            self._writes_since_prune = 0
            cutoff = received_at - timedelta(seconds=self.retention_seconds)
            try:
                await self.prune_before(cutoff)
            except LiveTickJournalError as error:
                self._maintenance_failures += 1
                self._last_maintenance_error = type(error.__cause__ or error).__name__
        return True

    async def list_recent(
        self,
        *,
        symbol: str,
        limit: int = 100,
    ) -> list[PersistedLiveTick]:
        page = await self.list_page(symbol=symbol, limit=limit)
        return list(page.items)

    async def list_page(
        self,
        *,
        symbol: str,
        limit: int = 100,
        cursor: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> PersistedLiveTickPage:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("symbol must not be empty")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        safe_limit = min(limit, 10_000)
        normalized_start = _require_aware(start_at, field="start_at")
        normalized_end = _require_aware(end_at, field="end_at")
        if (
            normalized_start is not None
            and normalized_end is not None
            and normalized_start > normalized_end
        ):
            raise ValueError("start_at must not be after end_at")

        conditions = [LiveMarketTickRecord.symbol == normalized_symbol]
        if normalized_start is not None:
            conditions.append(LiveMarketTickRecord.received_at >= normalized_start)
        if normalized_end is not None:
            conditions.append(LiveMarketTickRecord.received_at <= normalized_end)
        if cursor is not None:
            cursor_received_at, cursor_id = _decode_history_cursor(
                cursor,
                symbol=normalized_symbol,
                start_at=normalized_start,
                end_at=normalized_end,
            )
            conditions.append(
                or_(
                    LiveMarketTickRecord.received_at < cursor_received_at,
                    and_(
                        LiveMarketTickRecord.received_at == cursor_received_at,
                        LiveMarketTickRecord.id < cursor_id,
                    ),
                )
            )

        async with self.session_factory() as session:
            try:
                result = await session.scalars(
                    select(LiveMarketTickRecord)
                    .where(*conditions)
                    .order_by(
                        LiveMarketTickRecord.received_at.desc(),
                        LiveMarketTickRecord.id.desc(),
                    )
                    .limit(safe_limit + 1)
                )
                rows = list(result.all())
            except SQLAlchemyError as error:
                self._read_failures += 1
                self._last_read_error = type(error).__name__
                raise LiveTickJournalError("failed to read persisted live history") from error

        self._last_read_error = None
        has_more = len(rows) > safe_limit
        page_rows = rows[:safe_limit]
        next_cursor = None
        if has_more and page_rows:
            last_row = page_rows[-1]
            next_cursor = _encode_history_cursor(
                symbol=normalized_symbol,
                received_at=last_row.received_at,
                row_id=last_row.id,
                start_at=normalized_start,
                end_at=normalized_end,
            )
        return PersistedLiveTickPage(
            items=tuple(row.as_persisted() for row in page_rows),
            next_cursor=next_cursor,
        )

    async def prune_before(self, cutoff: datetime) -> int:
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")
        async with self.session_factory() as session:
            try:
                result = await session.execute(
                    delete(LiveMarketTickRecord).where(
                        LiveMarketTickRecord.received_at < cutoff
                    )
                )
                await session.commit()
            except SQLAlchemyError as error:
                await session.rollback()
                self._last_maintenance_error = type(error).__name__
                raise LiveTickJournalError("failed to prune persisted live history") from error
        deleted = int(result.rowcount or 0)
        self._pruned_rows += deleted
        self._last_prune_at = datetime.now(UTC)
        self._last_maintenance_error = None
        return deleted

    async def prune_expired(self, *, now: datetime | None = None) -> int:
        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return await self.prune_before(
            current_time - timedelta(seconds=self.retention_seconds)
        )

    def status(self) -> dict[str, object]:
        return {
            "backend": "sql",
            "healthy": self._last_write_error is None,
            "write_healthy": self._last_write_error is None,
            "read_healthy": self._last_read_error is None,
            "maintenance_healthy": self._last_maintenance_error is None,
            "writes_attempted": self._writes_attempted,
            "writes_inserted": self._writes_inserted,
            "idempotent_hits": self._idempotent_hits,
            "write_failures": self._write_failures,
            "read_failures": self._read_failures,
            "maintenance_failures": self._maintenance_failures,
            "pruned_rows": self._pruned_rows,
            "retention_seconds": self.retention_seconds,
            "prune_every_writes": self.prune_every_writes,
            "last_write_error": self._last_write_error,
            "last_read_error": self._last_read_error,
            "last_maintenance_error": self._last_maintenance_error,
            "last_write_success_at": (
                self._last_write_success_at.isoformat()
                if self._last_write_success_at is not None
                else None
            ),
            "last_prune_at": (
                self._last_prune_at.isoformat() if self._last_prune_at is not None else None
            ),
        }
