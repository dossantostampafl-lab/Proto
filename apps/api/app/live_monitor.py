from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime

from services.analytics.live_market import calculate_live_market_analytics
from services.market_data import (
    CoinbasePublicMarketDataAdapter,
    DataQualityIssue,
    DataQualityMonitor,
    LiveTickJournal,
    LiveTickJournalError,
    MarketTick,
    PersistedLiveTickPage,
    PublicMarketDataAdapter,
    compute_orderbook_metrics,
    evaluate_live_coverage,
)

from .event_state import event_runtime
from .live_payloads import age_seconds, market_payload, orderbook_payload, source_to_server_delta_ms
from .live_sequence import LiveSequenceState
from .metrics_state import metrics
from .models import SystemMode
from .websockets import hub

_HISTORY_LIMIT = 512
_STALE_AFTER_SECONDS = 10.0
_SOURCE_MESSAGE_STALE_SECONDS = 30.0


class LiveCryptoMonitor:
    def __init__(
        self,
        adapter: PublicMarketDataAdapter | None = None,
        *,
        journal: LiveTickJournal | None = None,
        persistence_required: bool = False,
    ) -> None:
        self._adapter = adapter or CoinbasePublicMarketDataAdapter()
        self._quality = DataQualityMonitor(stale_after_seconds=_STALE_AFTER_SECONDS)
        self._journal = journal
        self._persistence_required = persistence_required
        self._task: asyncio.Task[None] | None = None
        self._latest: dict[str, MarketTick] = {}
        self._history: dict[str, deque[MarketTick]] = defaultdict(
            lambda: deque(maxlen=_HISTORY_LIMIT)
        )
        self._received_at: dict[str, datetime] = {}
        self._sequence = LiveSequenceState()
        self._symbol_connection_generation: dict[str, int] = {}
        self._connection_generation = self._adapter.health().connection_generation
        self._last_error: str | None = None
        self._persisted_current_connection = 0
        self._persistence_idempotent_current_connection = 0
        self._persistence_write_failures_current_connection = 0
        self._persistence_read_failures = 0
        self._last_persistence_write_error: str | None = None
        self._last_persistence_read_error: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def expected_symbols(self) -> tuple[str, ...]:
        return tuple(self._adapter.symbols)

    def configure_persistence(
        self,
        journal: LiveTickJournal | None,
        *,
        required: bool,
    ) -> None:
        self._journal = journal
        self._persistence_required = required
        self._last_persistence_write_error = None
        self._last_persistence_read_error = None

    async def start(self) -> None:
        if self.running:
            return
        self._last_error = None
        self._task = asyncio.create_task(self._run(), name="live-crypto-monitor")
        metrics.increment("live_monitor_starts")
        await event_runtime.safe_publish(
            "proto.system",
            {
                "event": "live_monitor_started",
                "mode": SystemMode.LIVE_MONITORING.value,
                "financial_connectivity": "false",
                "real_money_execution": "false",
            },
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None
        metrics.increment("live_monitor_stops")
        await event_runtime.safe_publish(
            "proto.system",
            {
                "event": "live_monitor_stopped",
                "mode": SystemMode.LIVE_MONITORING.value,
            },
        )

    def source_health(self) -> dict[str, object]:
        now = datetime.now(UTC)
        health = self._adapter.health()
        last_message_age = age_seconds(health.last_message_at, now=now)
        last_tick_age = age_seconds(health.last_tick_at, now=now)
        message_fresh = bool(
            health.connected
            and last_message_age is not None
            and last_message_age <= _SOURCE_MESSAGE_STALE_SECONDS
        )
        return {
            "source": "PUBLIC_READ_ONLY",
            "server_observed_at": now.isoformat(),
            **asdict(health),
            "message_fresh": message_fresh,
            "last_message_age_seconds": (
                round(last_message_age, 6) if last_message_age is not None else None
            ),
            "last_tick_age_seconds": (
                round(last_tick_age, 6) if last_tick_age is not None else None
            ),
            "expected_symbols": list(self._adapter.symbols),
            "financial_connectivity": False,
            "real_money_execution": False,
        }

    def persistence_status(self) -> dict[str, object]:
        journal_status = dict(self._journal.status()) if self._journal is not None else {}
        backend_write_healthy = bool(journal_status.get("write_healthy", True))
        backend_read_healthy = bool(journal_status.get("read_healthy", True))
        configured = self._journal is not None
        healthy = bool(
            not self._persistence_required
            or (
                configured
                and backend_write_healthy
                and self._last_persistence_write_error is None
            )
        )
        return {
            "configured": configured,
            "required": self._persistence_required,
            "healthy": healthy,
            "write_healthy": healthy,
            "read_healthy": (
                backend_read_healthy and self._last_persistence_read_error is None
            ),
            "persisted_current_connection": self._persisted_current_connection,
            "idempotent_hits_current_connection": (
                self._persistence_idempotent_current_connection
            ),
            "write_failures_current_connection": (
                self._persistence_write_failures_current_connection
            ),
            "read_failures": self._persistence_read_failures,
            "last_write_error": self._last_persistence_write_error,
            "last_read_error": self._last_persistence_read_error,
            "journal": journal_status,
            "financial_connectivity": False,
            "real_money_execution": False,
        }

    def status(self) -> dict[str, object]:
        feed_health = self.source_health()
        coverage = evaluate_live_coverage(
            expected_symbols=self._adapter.symbols,
            latest=self._latest,
            symbol_connection_generation=self._symbol_connection_generation,
            current_generation=int(feed_health["connection_generation"]),
            connected=bool(feed_health["connected"]),
            stale_after_seconds=_STALE_AFTER_SECONDS,
            received_times=self._received_at,
        )
        sequence_rejections_total, sequence_rejections_by_symbol = (
            self._sequence.rejection_snapshot(tuple(self._adapter.symbols))
        )
        return {
            "mode": SystemMode.LIVE_MONITORING,
            "running": self.running,
            **coverage,
            "source_message_fresh": bool(feed_health["message_fresh"]),
            "source": "PUBLIC_READ_ONLY",
            "feed_health": feed_health,
            "persistence": self.persistence_status(),
            "financial_connectivity": False,
            "real_money_execution": False,
            "expected_symbols": list(self._adapter.symbols),
            "symbols": sorted(self._latest),
            "last_sequence_by_symbol": self._sequence.last_sequence_snapshot(),
            "sequence_rejections_current_connection": sequence_rejections_total,
            "sequence_rejections_by_symbol": sequence_rejections_by_symbol,
            "history_limit_per_symbol": _HISTORY_LIMIT,
            "last_error": self._last_error,
        }

    def snapshots(self) -> list[dict[str, object]]:
        return [
            self._payload_for_tick(self._latest[symbol]) for symbol in sorted(self._latest)
        ]

    def snapshot(self, symbol: str) -> dict[str, object] | None:
        tick = self._latest.get(symbol.upper())
        return self._payload_for_tick(tick) if tick is not None else None

    def analytics(self, symbol: str) -> dict[str, object] | None:
        normalized_symbol = symbol.upper()
        history = self._history.get(normalized_symbol)
        if not history:
            return None
        result = calculate_live_market_analytics(list(history))
        latest_tick = history[-1]
        received_at = self._received_at.get(normalized_symbol)
        return {
            **asdict(result),
            "latest_source_at": latest_tick.timestamp.isoformat(),
            "latest_received_at": received_at.isoformat() if received_at is not None else None,
            "latest_source_to_server_delta_ms": (
                source_to_server_delta_ms(
                    source_at=latest_tick.timestamp,
                    received_at=received_at,
                )
                if received_at is not None
                else None
            ),
            "source": "PUBLIC_READ_ONLY_DESCRIPTIVE",
            "financial_connectivity": False,
            "real_money_execution": False,
        }

    async def persisted_history_page(
        self,
        symbol: str,
        *,
        limit: int = 100,
        cursor: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> PersistedLiveTickPage | None:
        normalized_symbol = symbol.strip().upper()
        if normalized_symbol not in self._adapter.symbols:
            raise ValueError("symbol is outside the configured live allowlist")
        if self._journal is None:
            return None
        try:
            page = await self._journal.list_page(
                symbol=normalized_symbol,
                limit=limit,
                cursor=cursor,
                start_at=start_at,
                end_at=end_at,
            )
        except LiveTickJournalError as error:
            self._persistence_read_failures += 1
            self._last_persistence_read_error = type(error.__cause__ or error).__name__
            metrics.increment("live_market_persistence_read_failures")
            raise
        self._last_persistence_read_error = None
        return page

    async def persisted_history(
        self,
        symbol: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, object]] | None:
        page = await self.persisted_history_page(symbol, limit=limit)
        if page is None:
            return None
        return [row.as_dict() for row in page.items]

    def _sync_connection_generation(self, generation: int) -> None:
        if generation == self._connection_generation:
            return
        self._connection_generation = generation
        self._quality.reset()
        self._latest.clear()
        self._history.clear()
        self._received_at.clear()
        self._sequence.reset()
        self._symbol_connection_generation.clear()
        self._persisted_current_connection = 0
        self._persistence_idempotent_current_connection = 0
        self._persistence_write_failures_current_connection = 0
        self._last_persistence_write_error = None
        metrics.increment("live_market_connection_generation_changes")

    def _record_sequence_rejection(self, symbol: str, reason: str) -> None:
        self._sequence.record_rejection(symbol, reason)
        metrics.increment("live_market_sequence_rejections")
        metrics.increment(f"live_market_sequence_{reason}_rejections")

    async def _persist_before_accept(
        self,
        tick: MarketTick,
        *,
        received_at: datetime,
        connection_generation: int,
    ) -> bool:
        if self._journal is None:
            if not self._persistence_required:
                return True
            self._persistence_write_failures_current_connection += 1
            self._last_persistence_write_error = "JOURNAL_NOT_CONFIGURED"
            metrics.increment("live_market_persistence_write_failures")
            return False

        try:
            inserted = await self._journal.append(
                tick,
                received_at=received_at,
                connection_generation=connection_generation,
            )
        except LiveTickJournalError as error:
            self._persistence_write_failures_current_connection += 1
            self._last_persistence_write_error = type(error.__cause__ or error).__name__
            metrics.increment("live_market_persistence_write_failures")
            return not self._persistence_required

        self._last_persistence_write_error = None
        if inserted:
            self._persisted_current_connection += 1
            metrics.increment("live_market_persisted_ticks")
        else:
            self._persistence_idempotent_current_connection += 1
            metrics.increment("live_market_persistence_idempotent_hits")
        return True

    async def ingest_tick(self, tick: MarketTick) -> bool:
        if tick.symbol not in self._adapter.symbols:
            metrics.increment("live_market_unexpected_symbol")
            return False

        health = self._adapter.health()
        self._sync_connection_generation(health.connection_generation)
        report = self._quality.evaluate(tick)
        if not report.valid:
            metrics.increment("live_market_frames_rejected")
            for issue in report.issues:
                metrics.increment(f"live_data_quality_{issue.value.lower()}")
                if issue is DataQualityIssue.DUPLICATE_SEQUENCE:
                    self._record_sequence_rejection(tick.symbol, "duplicate")
                elif issue is DataQualityIssue.OUT_OF_ORDER_SEQUENCE:
                    self._record_sequence_rejection(tick.symbol, "regression")
            return False

        previous_sequence = self._sequence.previous(tick.symbol)
        if previous_sequence is not None:
            if tick.sequence == previous_sequence:
                self._record_sequence_rejection(tick.symbol, "duplicate")
                return False
            if tick.sequence < previous_sequence:
                self._record_sequence_rejection(tick.symbol, "regression")
                return False

        received_at = datetime.now(UTC)
        if not await self._persist_before_accept(
            tick,
            received_at=received_at,
            connection_generation=health.connection_generation,
        ):
            metrics.increment("live_market_frames_rejected")
            return False

        self._latest[tick.symbol] = tick
        self._history[tick.symbol].append(tick)
        self._received_at[tick.symbol] = received_at
        self._sequence.accept(tick.symbol, tick.sequence)
        self._symbol_connection_generation[tick.symbol] = health.connection_generation
        metrics.increment("live_market_frames")
        book = compute_orderbook_metrics(tick)
        await asyncio.gather(
            hub.broadcast(
                "market-data",
                {"type": "market-data", "data": self._payload_for_tick(tick)},
            ),
            hub.broadcast(
                "orderbook",
                {
                    "type": "orderbook",
                    "data": orderbook_payload(
                        tick,
                        book,
                        received_at=received_at,
                        connection_generation=health.connection_generation,
                    ),
                },
            ),
        )
        return True

    async def _run(self) -> None:
        try:
            async for tick in self._adapter.stream():
                await self.ingest_tick(tick)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._last_error = type(error).__name__
            metrics.increment("live_monitor_failures")
            await event_runtime.safe_publish(
                "proto.system",
                {
                    "event": "live_monitor_failure",
                    "error_type": self._last_error,
                },
            )

    def _payload_for_tick(self, tick: MarketTick) -> dict[str, object]:
        return market_payload(
            tick,
            received_at=self._received_at.get(tick.symbol),
            connection_generation=self._symbol_connection_generation.get(
                tick.symbol,
                self._connection_generation,
            ),
        )


live_monitor = LiveCryptoMonitor()
