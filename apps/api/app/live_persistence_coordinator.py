from __future__ import annotations

from datetime import datetime

from services.market_data import LiveTickJournal, LiveTickJournalError, MarketTick, PersistedLiveTickPage

from .metrics_state import metrics


class LivePersistenceCoordinator:
    """Own persistence state, health, and fail-closed live tick durability semantics."""

    def __init__(
        self,
        journal: LiveTickJournal | None = None,
        *,
        required: bool = False,
    ) -> None:
        self._journal = journal
        self._required = required
        self._persisted_current_connection = 0
        self._idempotent_current_connection = 0
        self._write_failures_current_connection = 0
        self._read_failures = 0
        self._last_write_error: str | None = None
        self._last_read_error: str | None = None

    def configure(self, journal: LiveTickJournal | None, *, required: bool) -> None:
        self._journal = journal
        self._required = required
        self._last_write_error = None
        self._last_read_error = None

    def reset_connection(self) -> None:
        self._persisted_current_connection = 0
        self._idempotent_current_connection = 0
        self._write_failures_current_connection = 0
        self._last_write_error = None

    def status(self) -> dict[str, object]:
        journal_status = dict(self._journal.status()) if self._journal is not None else {}
        backend_write_healthy = bool(journal_status.get("write_healthy", True))
        backend_read_healthy = bool(journal_status.get("read_healthy", True))
        configured = self._journal is not None
        healthy = bool(
            not self._required
            or (configured and backend_write_healthy and self._last_write_error is None)
        )
        return {
            "configured": configured,
            "required": self._required,
            "healthy": healthy,
            "write_healthy": healthy,
            "read_healthy": backend_read_healthy and self._last_read_error is None,
            "persisted_current_connection": self._persisted_current_connection,
            "idempotent_hits_current_connection": self._idempotent_current_connection,
            "write_failures_current_connection": self._write_failures_current_connection,
            "read_failures": self._read_failures,
            "last_write_error": self._last_write_error,
            "last_read_error": self._last_read_error,
            "journal": journal_status,
            "financial_connectivity": False,
            "real_money_execution": False,
        }

    async def persist_before_accept(
        self,
        tick: MarketTick,
        *,
        received_at: datetime,
        connection_generation: int,
    ) -> bool:
        if self._journal is None:
            if not self._required:
                return True
            self._write_failures_current_connection += 1
            self._last_write_error = "JOURNAL_NOT_CONFIGURED"
            metrics.increment("live_market_persistence_write_failures")
            return False

        try:
            inserted = await self._journal.append(
                tick,
                received_at=received_at,
                connection_generation=connection_generation,
            )
        except LiveTickJournalError as error:
            self._write_failures_current_connection += 1
            self._last_write_error = type(error.__cause__ or error).__name__
            metrics.increment("live_market_persistence_write_failures")
            return not self._required

        self._last_write_error = None
        if inserted:
            self._persisted_current_connection += 1
            metrics.increment("live_market_persisted_ticks")
        else:
            self._idempotent_current_connection += 1
            metrics.increment("live_market_persistence_idempotent_hits")
        return True

    async def history_page(
        self,
        *,
        symbol: str,
        limit: int,
        cursor: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> PersistedLiveTickPage | None:
        if self._journal is None:
            return None
        try:
            page = await self._journal.list_page(
                symbol=symbol,
                limit=limit,
                cursor=cursor,
                start_at=start_at,
                end_at=end_at,
            )
        except LiveTickJournalError as error:
            self._read_failures += 1
            self._last_read_error = type(error.__cause__ or error).__name__
            metrics.increment("live_market_persistence_read_failures")
            raise
        self._last_read_error = None
        return page
