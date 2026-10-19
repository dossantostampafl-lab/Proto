"""The isolated executor: the only process that holds venue credentials.

It runs the canonical OMS, ledger and reconciliation against Binance Spot
(testnet only) and accepts work only as intent + Risk Permit pairs from the
public API over the private network. It fails closed:

- every permit is verified (signature, fingerprint, expiry) and spent durably;
- a reconciliation divergence halts new orders until an operator releases it,
  and the release itself is refused while the divergence still holds;
- on start it recovers the persisted book and reconciles before taking orders;
- a real-money adapter is refused outright.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from services.execution import ReleaseStage, RiskPermit, TradeIntent, VenueId
from services.execution.venue import VenueAdapter
from services.oms import (
    ManagedOrder,
    OrderManager,
    SqlLedger,
    SqlOrderStore,
    SqlPermitLedger,
    ledger_metadata,
    opening_balances,
    reconcile_balances,
)
from services.risk import Ed25519PermitVerifier, PermitLedger, PermitSigner, PermitVerifier
from services.risk.permit_keys import decode_key
from services.venues.binance import BinanceSpotAdapter, load_instruments
from services.venues.credentials import load_credentials
from services.venues.xp_mt5 import XpMt5Adapter, bridge_client

from .settings import ExecutorSettings

logger = logging.getLogger("proto.executor")

AdapterFactory = Callable[[ExecutorSettings], Awaitable[BinanceSpotAdapter | XpMt5Adapter]]


class ExecutorHalted(RuntimeError):
    pass


async def binance_adapter(settings: ExecutorSettings) -> BinanceSpotAdapter:
    credentials = load_credentials(VenueId.BINANCE_SPOT, settings.environment, settings.account)
    client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    instruments = await load_instruments(
        settings.symbol_list, environment=settings.environment, client=client
    )
    if not instruments:
        await client.aclose()
        raise RuntimeError("no tradable instruments loaded from exchangeInfo")
    return BinanceSpotAdapter(credentials, instruments=instruments, client=client)


async def xp_adapter(settings: ExecutorSettings) -> XpMt5Adapter:
    """The MT5 bridge on the Windows host: the account's password never leaves that host."""

    assert settings.bridge_url is not None and settings.bridge_token is not None
    client = bridge_client(settings.bridge_url, settings.bridge_token.get_secret_value())
    try:
        return await XpMt5Adapter.connect(client, symbols=settings.symbol_list)
    except BaseException:
        await client.aclose()
        raise


def default_adapter_factory(settings: ExecutorSettings) -> AdapterFactory:
    return xp_adapter if settings.venue is VenueId.XP_B3 else binance_adapter


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Executor:
    def __init__(
        self,
        settings: ExecutorSettings,
        *,
        adapter_factory: AdapterFactory | None = None,
        engine: AsyncEngine | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.settings = settings
        self.venue = settings.venue
        self._adapter_factory = adapter_factory or default_adapter_factory(settings)
        self._engine = engine
        self._clock = clock
        self.adapter: VenueAdapter | None = None
        self.ledger: SqlLedger | None = None
        self.oms: OrderManager | None = None
        self.halted: str | None = None
        self.last_reconcile_at: datetime | None = None
        self.last_consistent: bool | None = None
        self.last_divergences: list[dict[str, Any]] = []
        self.events: deque[dict[str, Any]] = deque(maxlen=500)
        self._tasks: list[asyncio.Task] = []
        self._reconcile_lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return self.oms is not None

    async def start(self) -> None:
        adapter = await self._adapter_factory(self.settings)
        if adapter.real_money_execution:
            raise RuntimeError("no certified live scope: real-money adapters are refused")
        engine = self._engine or create_async_engine(self.settings.database_url)
        self._engine = engine
        async with engine.begin() as connection:
            await connection.run_sync(ledger_metadata.create_all)
        account_id = self.settings.account_id
        ledger = SqlLedger(engine, account_id=account_id, venue=self.venue)
        if not await ledger.balances():
            # First start: the ledger opens from what the account already holds.
            snapshot = await adapter.balances()
            await ledger.post(
                opening_balances(
                    snapshot, account_id=account_id, venue=self.venue, now=self._clock()
                )
            )
            self._event("LEDGER_OPENED", {"assets": sorted(snapshot)})
        signer: PermitVerifier
        if self.settings.permit_public_key:
            signer = Ed25519PermitVerifier(decode_key(self.settings.permit_public_key))
        else:
            assert self.settings.permit_signing_key is not None
            signer = PermitSigner(self.settings.permit_signing_key.get_secret_value().encode())
        self.oms = OrderManager(
            adapter=adapter,
            ledger=ledger,
            permits=SqlPermitLedger(PermitLedger(signer), engine),
            instruments=adapter.instruments,
            recorder=self._record,
            store=SqlOrderStore(engine, account_id=account_id),
            clock=self._clock,
        )
        self.adapter, self.ledger = adapter, ledger
        await self.oms.recover()
        await self.reconcile()

    def start_background(self) -> None:
        self._tasks = [
            asyncio.create_task(self._every(self.settings.poll_seconds, self.poll_open_orders)),
            asyncio.create_task(self._every(self.settings.reconcile_seconds, self.reconcile)),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        if self.adapter is not None:
            await self.adapter.aclose()
        if self._engine is not None:
            await self._engine.dispose()

    async def _every(self, seconds: float, job: Callable[[], Awaitable[Any]]) -> None:
        while True:
            await asyncio.sleep(seconds)
            try:
                await job()
            except Exception:  # keep supervising; the next round retries
                logger.exception("executor background job failed")

    # --- work ------------------------------------------------------------------

    def _oms(self) -> OrderManager:
        if self.oms is None:
            raise RuntimeError("executor not started")
        return self.oms

    async def submit(self, intent: TradeIntent, permit: RiskPermit) -> ManagedOrder:
        oms = self._oms()
        existing = next((o for o in oms.orders if o.intent.intent_id == intent.intent_id), None)
        if existing is not None:  # a retried dispatch of the same intent
            return existing
        if self.halted is not None:
            raise ExecutorHalted(self.halted)
        if intent.venue is not self.venue:
            raise ValueError(f"this executor only trades {self.venue.value}")
        if intent.stage not in (ReleaseStage.PAPER, ReleaseStage.SHADOW):
            # CANARY and LIVE move real money: never on a testnet executor.
            raise ValueError(f"{intent.stage.value} intents need a certified live executor")
        order = await oms.submit(intent, permit)
        await self.reconcile()
        return order

    async def poll_open_orders(self) -> None:
        """Pick up fills on resting orders (the venue is the source of truth)."""

        oms = self._oms()
        for order in oms.open_orders():
            try:
                await oms.refresh(order.client_order_id)
            except Exception:
                logger.exception("refresh failed for %s", order.client_order_id)

    async def reconcile(self) -> dict[str, Any]:
        assert self.ledger is not None and self.adapter is not None
        async with self._reconcile_lock:
            divergences = reconcile_balances(
                await self.ledger.balances(), await self.adapter.balances()
            )
            self.last_reconcile_at = self._clock()
            self.last_consistent = not divergences
            self.last_divergences = [d.to_dict() for d in divergences]
            if divergences and self.halted is None:
                self.halted = "reconciliation divergence"
                self._event(
                    "RECONCILIATION_DIVERGENCE",
                    {"divergences": [d.to_dict() for d in divergences]},
                )
                logger.error("executor halted: %s", [d.to_dict() for d in divergences])
            return {
                "venue": self.venue.value,
                "consistent": not divergences,
                "divergences": [d.to_dict() for d in divergences],
                "venue_halted": self.halted is not None,
            }

    def watchdog_halt(self, watchdog_id: str, reasons: list[str]) -> bool:
        """Stop accepting orders. Returns False when already halted; never releases."""

        if self.halted is not None:
            return False
        self.halted = f"watchdog: {', '.join(reasons)[:200]}"
        self._event("WATCHDOG_HALT", {"watchdog_id": watchdog_id, "reasons": reasons})
        logger.error("executor halted by the watchdog: %s", reasons)
        return True

    def watchdog_status(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "halted": self.halted,
            "last_reconcile_at": (
                self.last_reconcile_at.isoformat() if self.last_reconcile_at else None
            ),
            "consistent": self.last_consistent,
        }

    async def release_halt(self) -> dict[str, Any]:
        """Operator action. Refused while the ledger and the venue still disagree."""

        assert self.ledger is not None and self.adapter is not None
        divergences = reconcile_balances(
            await self.ledger.balances(), await self.adapter.balances()
        )
        if divergences:
            raise ExecutorHalted("still diverged from the venue")
        self.halted = None
        self._event("HALT_RELEASED", {})
        return await self.reconcile()

    # --- audit -------------------------------------------------------------------

    def _event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append(
            {"event_type": event_type, "at": self._clock().isoformat(), "payload": payload}
        )

    async def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        self._event(event_type, payload)
        logger.info("%s %s", event_type, payload.get("client_order_id"))
