"""Managed Phase 4 PAPER execution and reconciliation worker.

No credentials or live adapter are constructed. The canonical paper OMS is
the sole venue sink; the lifecycle owns its supervised reconciliation loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncEngine

from services.risk.engine import AccountState
from services.risk.permits import PermitSigner

from .contracts import ExecutionReport, RiskPermit, TradableInstrument, TradeIntent, VenueId
from .oms import CanonicalPaperOMS, OrderRejected
from .venue import PaperVenueAdapter, Quote


class Phase4PaperRuntime:
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        signer: PermitSigner,
        instruments: tuple[TradableInstrument, ...],
        quotes: Callable[[str], Quote | None],
        starting_balances: Mapping[str, Decimal],
        fee_bps: Decimal,
        slippage_bps: Decimal,
        reconciliation_interval_seconds: float = 5.0,
    ) -> None:
        if not instruments:
            raise ValueError("paper execution requires certified instruments")
        venue = instruments[0].venue
        if venue not in {VenueId.PAPER, VenueId.BINANCE_SPOT}:
            raise ValueError("paper execution venue is not certified")
        if any(item.venue is not venue or item.quote_asset != "BRL" for item in instruments):
            raise ValueError("paper execution requires a single BRL-quoted venue")
        if reconciliation_interval_seconds <= 0:
            raise ValueError("reconciliation interval must be positive")
        self.quotes = quotes
        self.adapter = PaperVenueAdapter(
            instruments=instruments,
            quotes=quotes,
            balances=starting_balances,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            simulated_venue=venue,
        )
        self.oms = CanonicalPaperOMS(
            engine, self.adapter, signer, instruments={i.instrument_id: i for i in instruments}
        )
        self._interval = reconciliation_interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.background_failure: str | None = None

    @property
    def real_money_execution(self) -> bool:
        return False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done() and self.background_failure is None

    async def start(self, *, now: datetime) -> None:
        if self.running:
            return
        if self._task is not None and self.background_failure is not None:
            raise OrderRejected("reconciliation_supervisor_failed")
        await self.oms.bootstrap(now=now)
        result = await self.oms.reconcile(tolerance=Decimal("0"))
        if not result["ok"]:
            raise OrderRejected("startup_reconciliation_failed")
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self.oms.reconciliation_loop(
                self._stop,
                interval_seconds=self._interval,
                tolerance=Decimal("0"),
            ),
            name="proto-canonical-paper-reconciliation",
        )
        self._task.add_done_callback(self._record_supervisor_result)

    def _record_supervisor_result(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            exception = task.exception()
        except asyncio.InvalidStateError:
            return
        if exception is not None:
            self.background_failure = type(exception).__name__
            self.oms._local_halt = True
        elif not self._stop.is_set():
            self.background_failure = "reconciliation_stopped_unexpectedly"
            self.oms._local_halt = True

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None:
            try:
                await task
            except Exception as exc:
                self.background_failure = type(exc).__name__
                self.oms._local_halt = True

    async def risk_account(self, *, now: datetime) -> AccountState:
        if not self.running:
            raise OrderRejected("paper_runtime_not_running")
        return await self.oms.risk_account(now=now, quotes=self.quotes)

    async def execute_authorized(
        self,
        intent: TradeIntent,
        permit: RiskPermit,
        *,
        now: datetime,
    ) -> ExecutionReport:
        if not self.running:
            raise OrderRejected("paper_runtime_not_running")
        if intent.venue is not self.adapter.venue:
            raise OrderRejected("paper_venue_mismatch")
        return await self.oms.place(intent, permit, now=now)
