"""Fail-closed canonical OMS for paper execution (Phase 4).

Persist the permission and order before calling the adapter; never submit
a second time after an uncertain outcome. Report application, balances,
reservations and fees share one SQL transaction. No real venue path exists
here: financial_connectivity=false and real_money_execution=false.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from services.risk.durable_permits import DurablePermitLedger
from services.risk.engine import AccountState
from services.risk.permits import PermitSigner

from .contracts import (
    TERMINAL_ORDER_STATUSES,
    ExecutionReport,
    OrderStatus,
    OrderType,
    ReleaseStage,
    RiskPermit,
    TradableInstrument,
    TradeIntent,
    transition,
)
from .venue import PaperVenueAdapter, VenueAdapter, VenueOrder

oms_metadata = MetaData()

order_records = Table(
    "execution_orders",
    oms_metadata,
    Column("client_order_id", String(36), primary_key=True),
    Column("intent_id", String(36), nullable=False, unique=True),
    Column("intent_fingerprint", String(64), nullable=False),
    Column("permit_id", String(36), nullable=False, unique=True),
    Column("venue", String(40), nullable=False),
    Column("instrument_id", String(160), nullable=False),
    Column("side", String(4), nullable=False),
    Column("quantity", String(80), nullable=False),
    Column("order_type", String(16), nullable=False),
    Column("limit_price", String(80), nullable=False),
    Column("status", String(32), nullable=False),
    Column("filled_quantity", String(80), nullable=False),
    Column("average_price", String(80), nullable=True),
    Column("cumulative_notional", String(80), nullable=False),
    Column("fee", String(80), nullable=False),
    Column("fee_asset", String(20), nullable=True),
    Column("venue_order_id", String(80), nullable=True),
    Column("reason", String(200), nullable=True),
    Column("reported_at", DateTime(timezone=True), nullable=False),
)

fill_records = Table(
    "execution_fills",
    oms_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "client_order_id",
        String(36),
        ForeignKey("execution_orders.client_order_id"),
        nullable=False,
    ),
    Column("venue_order_id", String(80), nullable=True),
    Column("delta_quantity", String(80), nullable=False),
    Column("cumulative_quantity", String(80), nullable=False),
    Column("delta_notional", String(80), nullable=False),
    Column("delta_fee", String(80), nullable=False),
    Column("fee_asset", String(20), nullable=True),
    Column("reported_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "client_order_id", "cumulative_quantity", name="uq_execution_fill_cumulative"
    ),
)

balance_entries = Table(
    "execution_balance_entries",
    oms_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "client_order_id",
        String(36),
        ForeignKey("execution_orders.client_order_id"),
        nullable=False,
    ),
    Column("venue", String(40), nullable=False),
    Column("asset", String(20), nullable=False),
    Column("amount", String(80), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
)

opening_balances = Table(
    "execution_opening_balances",
    oms_metadata,
    Column("venue", String(40), primary_key=True),
    Column("asset", String(20), primary_key=True),
    Column("balance", String(80), nullable=False),
)

reservations = Table(
    "execution_reservations",
    oms_metadata,
    Column(
        "client_order_id",
        String(36),
        ForeignKey("execution_orders.client_order_id"),
        primary_key=True,
    ),
    Column("venue", String(40), nullable=False),
    Column("asset", String(20), nullable=False),
    Column("total", String(80), nullable=False),
    Column("remaining", String(80), nullable=False),
)

equity_checkpoints = Table(
    "execution_equity_checkpoints",
    oms_metadata,
    Column("venue", String(40), primary_key=True),
    Column("day", String(10), nullable=False),
    Column("start_of_day_equity", String(80), nullable=False),
    Column("peak_equity", String(80), nullable=False),
)

venue_halts = Table(
    "execution_venue_halts",
    oms_metadata,
    Column("venue", String(40), primary_key=True),
    Column("reason", String(200), nullable=False),
    Column("halted_at", DateTime(timezone=True), nullable=False),
)


class OrderRejected(RuntimeError):
    """Refusal without any new venue order being sent."""


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _str(value: Decimal) -> str:
    return str(value)


class CanonicalPaperOMS:
    """An intentionally paper-only order authority with durable replay safety.

    Callers must provision the Phase 4 migrations; this class never silently
    creates tables or substitutes an in-memory store for failed persistence.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        adapter: VenueAdapter,
        signer: PermitSigner,
        *,
        instruments: Mapping[str, TradableInstrument],
    ) -> None:
        self.engine = engine
        self.adapter = adapter
        self._permits = DurablePermitLedger(engine, signer)
        self._instruments = dict(instruments)
        self._lock = asyncio.Lock()
        self._local_halt = False

    async def _row(self, client_order_id: str) -> Mapping | None:
        async with self.engine.connect() as connection:
            return (
                await connection.execute(
                    select(order_records).where(
                        order_records.c.client_order_id == client_order_id
                    )
                )
            ).mappings().first()

    @staticmethod
    def _report(row: Mapping) -> ExecutionReport:
        from .contracts import VenueId

        return ExecutionReport(
            venue=VenueId(row["venue"]),
            client_order_id=row["client_order_id"],
            venue_order_id=row["venue_order_id"],
            instrument_id=row["instrument_id"],
            side=row["side"],
            status=OrderStatus(row["status"]),
            quantity=Decimal(row["quantity"]),
            filled_quantity=Decimal(row["filled_quantity"]),
            average_price=(
                Decimal(row["average_price"]) if row["average_price"] is not None else None
            ),
            fee=Decimal(row["fee"]),
            fee_asset=row["fee_asset"],
            reason=row["reason"],
            reported_at=_utc(row["reported_at"]),
        )

    async def _halted(self, connection: AsyncConnection) -> bool:
        if self._local_halt:
            return True
        return (
            await connection.scalar(
                select(venue_halts.c.venue).where(
                    venue_halts.c.venue == self.adapter.venue.value
                )
            )
        ) is not None

    async def _halt(self, reason: str, now: datetime) -> None:
        self._local_halt = True
        try:
            async with self.engine.begin() as connection:
                already = await connection.scalar(
                    select(venue_halts.c.venue).where(
                        venue_halts.c.venue == self.adapter.venue.value
                    )
                )
                if already is None:
                    await connection.execute(
                        insert(venue_halts).values(
                            venue=self.adapter.venue.value,
                            reason=reason[:200],
                            halted_at=now,
                        )
                    )
        except IntegrityError:
            # Concurrent watchdog and order paths may latch the same venue
            # simultaneously. The persisted first halt wins; duplicates
            # are idempotent rather than supervisor failures.
            async with self.engine.connect() as connection:
                existing = await connection.scalar(
                    select(venue_halts.c.venue).where(
                        venue_halts.c.venue == self.adapter.venue.value
                    )
                )
            if existing is None:
                raise
        except Exception:
            # Never forget the local halt when durable persistence has failed.
            self._local_halt = True
            raise

    async def _balances_locked(self, connection: AsyncConnection) -> dict[str, Decimal]:
        rows = (
            await connection.execute(
                select(opening_balances).where(
                    opening_balances.c.venue == self.adapter.venue.value
                )
            )
        ).mappings().all()
        totals: dict[str, Decimal] = defaultdict(Decimal)
        for row in rows:
            totals[row["asset"]] += Decimal(row["balance"])
        entries = (
            await connection.execute(
                select(balance_entries).where(
                    balance_entries.c.venue == self.adapter.venue.value
                )
            )
        ).mappings().all()
        for entry in entries:
            totals[entry["asset"]] += Decimal(entry["amount"])
        return dict(totals)

    async def _available_locked(self, connection: AsyncConnection, asset: str) -> Decimal:
        cash = (await self._balances_locked(connection)).get(asset, Decimal("0"))
        used = (
            await connection.execute(
                select(reservations).where(
                    reservations.c.venue == self.adapter.venue.value,
                    reservations.c.asset == asset,
                )
            )
        ).mappings().all()
        return cash - sum((Decimal(row["remaining"]) for row in used), Decimal("0"))

    async def _reserve(
        self,
        connection: AsyncConnection,
        intent: TradeIntent,
        order_id: str,
    ) -> None:
        instrument = self._instruments.get(intent.instrument_id)
        if instrument is None or instrument.venue is not intent.venue:
            raise OrderRejected("instrument_not_certified")
        # A LIMIT bound is required for a worst-case deterministic reservation.
        if intent.order_type is not OrderType.LIMIT or intent.limit_price is None:
            raise OrderRejected("unbounded_market_order")
        if instrument.order_violation(intent.quantity, intent.limit_price) is not None:
            raise OrderRejected("invalid_venue_grid")
        if intent.side == "BUY":
            asset = instrument.quote_asset
            if not isinstance(self.adapter, PaperVenueAdapter):
                raise OrderRejected("paper_fee_schedule_unavailable")
            amount = intent.quantity * intent.limit_price * (
                Decimal("1") + self.adapter.max_fee_bps / Decimal("10000")
            )
        else:
            if not intent.reduce_only:
                raise OrderRejected("sell_requires_reduce_only")
            asset = instrument.base_asset
            amount = intent.quantity
        # Serialize reservations against the same asset under PostgreSQL.
        # On SQLite, a writer conflict fails closed rather than double-spending.
        locked = await connection.scalar(
            select(opening_balances.c.asset)
            .where(
                opening_balances.c.venue == intent.venue.value,
                opening_balances.c.asset == asset,
            )
            .with_for_update()
        )
        if locked is None:
            raise OrderRejected("ledger_asset_not_initialized")
        if await self._available_locked(connection, asset) < amount:
            raise OrderRejected("insufficient_available_balance")
        await connection.execute(
            insert(reservations).values(
                client_order_id=order_id,
                venue=intent.venue.value,
                asset=asset,
                total=_str(amount),
                remaining=_str(amount),
            )
        )

    async def _seed(
        self,
        connection: AsyncConnection,
        snapshot: Mapping[str, Decimal],
        now: datetime,
    ) -> None:
        """Initialize the paper account baseline once, without submitting."""
        seeded = await connection.scalar(
            select(opening_balances.c.asset)
            .where(opening_balances.c.venue == self.adapter.venue.value)
            .limit(1)
        )
        if seeded is None:
            # The paper adapter may omit zero-balance assets. Persist
            # known base and quote assets at zero so a later BUY can
            # be followed by a reduce-only SELL under the same lock.
            initial = {asset: Decimal(value) for asset, value in snapshot.items()}
            for known in self._instruments.values():
                if known.venue is self.adapter.venue:
                    initial.setdefault(known.base_asset, Decimal("0"))
                    initial.setdefault(known.quote_asset, Decimal("0"))
            # All risk limits are denominated in BRL. Initial
            # non-BRL inventory cannot be valued without marked
            # prices and must not establish a fictitious baseline.
            if initial.get("BRL", Decimal("0")) <= 0 or any(
                amount != 0 for asset, amount in initial.items() if asset != "BRL"
            ):
                raise OrderRejected("opening_equity_not_certified")
            for asset, amount in initial.items():
                await connection.execute(
                    insert(opening_balances).values(
                        venue=self.adapter.venue.value,
                        asset=asset,
                        balance=_str(amount),
                    )
                )
            await connection.execute(
                insert(equity_checkpoints).values(
                    venue=self.adapter.venue.value,
                    day=now.date().isoformat(),
                    start_of_day_equity=_str(initial["BRL"]),
                    peak_equity=_str(initial["BRL"]),
                )
            )

    async def bootstrap(self, *, now: datetime) -> None:
        """Prepare durable paper balances and the constitutional BRL baseline."""

        if self.adapter.real_money_execution:
            raise OrderRejected("paper_only")
        async with self._lock:
            snapshot = await self.adapter.balances()
            async with self.engine.begin() as connection:
                if await self._halted(connection):
                    raise OrderRejected("venue_halted")
                await self._seed(connection, snapshot, now)

    async def place(
        self,
        intent: TradeIntent,
        permit: RiskPermit,
        *,
        now: datetime,
    ) -> ExecutionReport:
        """Persist authority and order atomically; submit to the venue once."""

        if (
            intent.stage is not ReleaseStage.PAPER
            or self.adapter.real_money_execution
            or self.adapter.venue is not intent.venue
        ):
            raise OrderRejected("paper_only")
        order_id = intent.client_order_id(0)
        async with self._lock:
            prior = await self._row(order_id)
            if prior is not None:
                if prior["intent_fingerprint"] != intent.fingerprint():
                    raise OrderRejected("client_order_collision")
                if prior["status"] == OrderStatus.UNKNOWN.value:
                    return await self.resolve_unknown(order_id)
                return self._report(prior)

            # Read before submission. Opening balances must be committed
            # before any new fills can change the external paper account.
            snapshot = await self.adapter.balances()
            instrument = self._instruments.get(intent.instrument_id)
            if instrument is None:
                raise OrderRejected("instrument_not_certified")
            try:
                async with self.engine.begin() as connection:
                    if await self._halted(connection):
                        raise OrderRejected("venue_halted")

                    await self._seed(connection, snapshot, now)
                    # This reservation and the consumed permit rollback together
                    # if any part of the order INSERT fails.
                    await self._reserve(connection, intent, order_id)
                    await self._permits.consume_in_transaction(
                        connection, permit, intent, now=now
                    )
                    await connection.execute(
                        insert(order_records).values(
                            client_order_id=order_id,
                            intent_id=str(intent.intent_id),
                            intent_fingerprint=intent.fingerprint(),
                            permit_id=str(permit.permit_id),
                            venue=intent.venue.value,
                            instrument_id=intent.instrument_id,
                            side=intent.side,
                            quantity=_str(intent.quantity),
                            order_type=intent.order_type.value,
                            limit_price=_str(intent.limit_price),
                            status=OrderStatus.SUBMITTED.value,
                            filled_quantity="0",
                            average_price=None,
                            cumulative_notional="0",
                            fee="0",
                            fee_asset=None,
                            venue_order_id=None,
                            reason=None,
                            reported_at=now,
                        )
                    )
            except IntegrityError as exc:
                prior = await self._row(order_id)
                if prior is not None and prior["intent_fingerprint"] == intent.fingerprint():
                    return self._report(prior)
                raise OrderRejected("order_or_permit_already_used") from exc

            order = VenueOrder(
                client_order_id=order_id,
                instrument_id=intent.instrument_id,
                side=intent.side,
                quantity=intent.quantity,
                order_type=intent.order_type,
                limit_price=intent.limit_price,
                time_in_force=intent.time_in_force,
                reduce_only=intent.reduce_only,
            )
            try:
                report = await self.adapter.submit(order)
                committed = await self._apply(report)
                if committed.status is OrderStatus.UNKNOWN:
                    await self._halt("adapter_report_unknown", now)
                return committed
            except Exception:
                # No retry here: the adapter may have filled before losing ACK.
                await self._mark_unknown(order_id, now)
                prior = await self._row(order_id)
                assert prior is not None
                return self._report(prior)

    async def _mark_unknown(self, order_id: str, now: datetime) -> None:
        self._local_halt = True
        async with self.engine.begin() as connection:
            row = (
                await connection.execute(
                    select(order_records).where(
                        order_records.c.client_order_id == order_id
                    )
                )
            ).mappings().first()
            if row is not None and OrderStatus(row["status"]) not in TERMINAL_ORDER_STATUSES:
                await connection.execute(
                    update(order_records)
                    .where(order_records.c.client_order_id == order_id)
                    .values(status=OrderStatus.UNKNOWN.value, reported_at=now)
                )
        await self._halt("unresolved_order_unknown", now)

    async def _apply(self, report: ExecutionReport) -> ExecutionReport:
        """Cumulative venue report -> unique delta movements, in one transaction."""

        async with self.engine.begin() as connection:
            row = (
                await connection.execute(
                    select(order_records)
                    .where(order_records.c.client_order_id == report.client_order_id)
                    .with_for_update()
                )
            ).mappings().first()
            if row is None:
                raise OrderRejected("unknown_client_order")
            if (
                report.venue.value != row["venue"]
                or report.instrument_id != row["instrument_id"]
                or report.side != row["side"]
                or report.quantity != Decimal(row["quantity"])
            ):
                raise OrderRejected("venue_report_identity_mismatch")

            current = OrderStatus(row["status"])
            if current is report.status:
                if (
                    report.filled_quantity == Decimal(row["filled_quantity"])
                    and report.fee == Decimal(row["fee"])
                    and (
                        str(report.average_price) if report.average_price is not None else None
                    ) == row["average_price"]
                ):
                    return self._report(row)
            else:
                transition(current, report.status)

            last_qty = Decimal(row["filled_quantity"])
            last_fee = Decimal(row["fee"])
            last_notional = Decimal(row["cumulative_notional"])
            new_notional = (
                report.filled_quantity * report.average_price
                if report.average_price is not None
                else Decimal("0")
            )
            delta_qty = report.filled_quantity - last_qty
            delta_fee = report.fee - last_fee
            delta_notional = new_notional - last_notional
            if (
                delta_qty < 0
                or delta_fee < 0
                or delta_notional < 0
                or (delta_qty == 0 and delta_notional != 0)
                or (
                    last_fee > 0
                    and row["fee_asset"] is not None
                    and report.fee_asset != row["fee_asset"]
                )
            ):
                raise OrderRejected("non_monotonic_venue_report")
            instrument = self._instruments[row["instrument_id"]]
            if delta_qty > 0:
                await connection.execute(
                    insert(fill_records).values(
                        client_order_id=report.client_order_id,
                        venue_order_id=report.venue_order_id,
                        delta_quantity=_str(delta_qty),
                        cumulative_quantity=_str(report.filled_quantity),
                        delta_notional=_str(delta_notional),
                        delta_fee=_str(delta_fee),
                        fee_asset=report.fee_asset,
                        reported_at=report.reported_at,
                    )
                )
                sign = Decimal("1") if row["side"] == "BUY" else Decimal("-1")
                for asset, amount in (
                    (instrument.base_asset, delta_qty * sign),
                    (instrument.quote_asset, -delta_notional * sign),
                ):
                    if amount != 0:
                        await connection.execute(
                            insert(balance_entries).values(
                                client_order_id=report.client_order_id,
                                venue=row["venue"],
                                asset=asset,
                                amount=_str(amount),
                                kind="TRADE",
                                recorded_at=report.reported_at,
                            )
                        )
            if delta_fee > 0:
                if not report.fee_asset:
                    raise OrderRejected("missing_fee_asset")
                await connection.execute(
                    insert(balance_entries).values(
                        client_order_id=report.client_order_id,
                        venue=row["venue"],
                        asset=report.fee_asset,
                        amount=_str(-delta_fee),
                        kind="FEE",
                        recorded_at=report.reported_at,
                    )
                )

            # Compare-and-swap guards SQLite too (FOR UPDATE works on Postgres).
            changed = await connection.execute(
                update(order_records)
                .where(
                    order_records.c.client_order_id == report.client_order_id,
                    order_records.c.status == row["status"],
                    order_records.c.filled_quantity == row["filled_quantity"],
                    order_records.c.fee == row["fee"],
                )
                .values(
                    status=report.status.value,
                    filled_quantity=_str(report.filled_quantity),
                    average_price=(
                        _str(report.average_price) if report.average_price is not None else None
                    ),
                    cumulative_notional=_str(new_notional),
                    fee=_str(report.fee),
                    fee_asset=report.fee_asset,
                    venue_order_id=report.venue_order_id,
                    reason=report.reason,
                    reported_at=report.reported_at,
                )
            )
            if changed.rowcount != 1:
                raise OrderRejected("concurrent_order_report")
            remaining = (
                Decimal("0")
                if report.status in TERMINAL_ORDER_STATUSES
                else Decimal("1") - report.filled_quantity / Decimal(row["quantity"])
            )
            reserved = (
                await connection.execute(
                    select(reservations).where(
                        reservations.c.client_order_id == report.client_order_id
                    )
                )
            ).mappings().first()
            assert reserved is not None
            await connection.execute(
                update(reservations)
                .where(reservations.c.client_order_id == report.client_order_id)
                .values(remaining=_str(Decimal(reserved["total"]) * remaining))
            )
            return report

    async def refresh(self, client_order_id: str) -> ExecutionReport:
        """Poll the venue for a cumulative status; never submit again."""

        row = await self._row(client_order_id)
        if row is None:
            raise OrderRejected("unknown_client_order")
        if OrderStatus(row["status"]) in TERMINAL_ORDER_STATUSES:
            return self._report(row)
        try:
            report = await self.adapter.query(client_order_id)
        except Exception:
            report = None
        if report is None:
            await self._mark_unknown(client_order_id, datetime.now(UTC))
            latest = await self._row(client_order_id)
            assert latest is not None
            return self._report(latest)
        try:
            result = await self._apply(report)
        except Exception:
            await self._mark_unknown(client_order_id, datetime.now(UTC))
            latest = await self._row(client_order_id)
            assert latest is not None
            return self._report(latest)
        if result.status is OrderStatus.UNKNOWN:
            await self._mark_unknown(client_order_id, datetime.now(UTC))
        return result

    async def resolve_unknown(self, client_order_id: str) -> ExecutionReport:
        """The only path out of UNKNOWN is a query, never a second submit."""

        return await self.refresh(client_order_id)

    async def cancel(self, client_order_id: str) -> ExecutionReport:
        row = await self._row(client_order_id)
        if row is None:
            raise OrderRejected("unknown_client_order")
        if row["status"] == OrderStatus.UNKNOWN.value:
            return await self.resolve_unknown(client_order_id)
        if OrderStatus(row["status"]) in TERMINAL_ORDER_STATUSES:
            return self._report(row)
        try:
            return await self._apply(await self.adapter.cancel(client_order_id))
        except Exception:
            await self._mark_unknown(client_order_id, datetime.now(UTC))
            row = await self._row(client_order_id)
            assert row is not None
            return self._report(row)

    async def reconcile(self, *, tolerance: Decimal = Decimal("0")) -> dict[str, object]:
        """Serialize reconciliation with order dispatch to avoid false drift halts."""

        async with self._lock:
            return await self._reconcile_locked(tolerance=tolerance)

    async def _reconcile_locked(self, *, tolerance: Decimal) -> dict[str, object]:
        """Mismatch against paper venue halts new orders (never auto releases)."""

        if tolerance < 0:
            raise ValueError("tolerance must be >= 0")
        # Read venue reports for all unsettled orders before balance comparison.
        # This captures late and partial fills, and marks missing orders UNKNOWN.
        async with self.engine.connect() as connection:
            pending = (
                await connection.execute(
                    select(order_records.c.client_order_id).where(
                        order_records.c.venue == self.adapter.venue.value,
                        order_records.c.status.not_in(
                            [item.value for item in TERMINAL_ORDER_STATUSES]
                        ),
                    )
                )
            ).scalars().all()
        for order_id in pending:
            await self.refresh(order_id)

        async with self.engine.connect() as connection:
            initialized = await connection.scalar(
                select(opening_balances.c.asset)
                .where(opening_balances.c.venue == self.adapter.venue.value)
                .limit(1)
            )
            if initialized is None:
                return {"ok": False, "reason": "ledger_uninitialized", "differences": {}}
            expected = await self._balances_locked(connection)
        actual = await self.adapter.balances()
        diffs = {
            asset: _str(expected.get(asset, Decimal("0")) - Decimal(actual.get(asset, 0)))
            for asset in expected.keys() | actual.keys()
            if abs(expected.get(asset, Decimal("0")) - Decimal(actual.get(asset, 0)))
            > tolerance
        }
        if diffs:
            await self._halt("balance_divergence", datetime.now(UTC))
        return {"ok": not diffs, "differences": diffs, "venue": self.adapter.venue.value}

    async def risk_account(self, *, now: datetime, quotes) -> AccountState:
        """Materialize the risk governor's account from persisted BRL ledger marks."""

        from .accounting import risk_account

        return await risk_account(self, now=now, quotes=quotes)

    async def reconciliation_loop(
        self,
        stop: asyncio.Event,
        *,
        interval_seconds: float = 5.0,
        tolerance: Decimal = Decimal("0"),
    ) -> None:
        """Run recurrent independent balance checks until the host stops it.

        The executor must supervise this worker; unexpected database or venue
        failures latch a local halt, rather than continuing execution blind.
        """

        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        while not stop.is_set():
            try:
                await self.reconcile(tolerance=tolerance)
            except Exception:
                self._local_halt = True
                raise
            if stop.is_set():
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
