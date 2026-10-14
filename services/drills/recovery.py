"""Recovery drill: lost responses, lost requests and crashes, recovered from durable state.

Each case runs the canonical OMS against the paper venue on a fresh SQLite file, "kills" the
process mid-flight, and boots a new one on the same database. The new process must resolve every
order by asking the venue (never by resending), keep the ledger reconciled with the venue, and
refuse a permit that was already spent before the crash.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from services.execution import OrderType, TradeIntent, VenueId
from services.execution.catalog import PAPER_INSTRUMENTS
from services.execution.venue import PaperVenueAdapter, Quote, VenueOrder
from services.oms import (
    OrderManager,
    OrderRejected,
    SqlLedger,
    SqlOrderStore,
    SqlPermitLedger,
    ledger_metadata,
    opening_balances,
    reconcile_balances,
)
from services.risk import PermitLedger, PermitSigner

from .report import DrillReport

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
BTC_ID = "BINANCE_SPOT:BTCUSDT"
QUOTE = Quote(bid=Decimal("62000.00"), ask=Decimal("62000.20"), observed_at=NOW)
OPENING = {"USDT": Decimal("10000")}
SIGNER = PermitSigner(b"drill-recovery-hmac-key-00000001")
ACCOUNT = "drill-recovery"


class CountingVenue(PaperVenueAdapter):
    """The paper venue, counting what actually reached it."""

    submissions = 0

    async def submit(self, order: VenueOrder):
        self.submissions += 1
        return await super().submit(order)


class LostResponseVenue(CountingVenue):
    """Executes the order, but the answer never comes back."""

    async def submit(self, order: VenueOrder):
        await super().submit(order)
        raise TimeoutError("response lost")


class LostRequestVenue(CountingVenue):
    """The request never reaches the venue."""

    async def submit(self, order: VenueOrder):
        raise ConnectionError("connection reset")


def _venue(kind: type[CountingVenue] = CountingVenue) -> CountingVenue:
    return kind(
        instruments=(PAPER_INSTRUMENTS[BTC_ID],),
        quotes=lambda _id: QUOTE,
        balances=dict(OPENING),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        clock=lambda: NOW,
        simulated_venue=VenueId.BINANCE_SPOT,
    )


def _intent(n: int, **overrides) -> TradeIntent:
    values = {
        "intent_id": UUID(int=n),
        "instrument_id": BTC_ID,
        "venue": VenueId.BINANCE_SPOT,
        "side": "BUY",
        "quantity": Decimal("0.01"),
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal("62100.00"),
        "strategy_id": "drill-recovery",
        "model_version": "drill",
        "signal_ids": (UUID(int=10_000 + n),),
        "created_at": NOW,
    }
    values.update(overrides)
    return TradeIntent.model_validate(values)


async def _boot(engine: AsyncEngine, venue: CountingVenue) -> tuple[OrderManager, SqlLedger]:
    """One process start on the shared database."""

    ledger = SqlLedger(engine, account_id=ACCOUNT, venue=VenueId.BINANCE_SPOT)
    await ledger.post(
        opening_balances(OPENING, account_id=ACCOUNT, venue=VenueId.BINANCE_SPOT, now=NOW)
    )
    oms = OrderManager(
        adapter=venue,
        ledger=ledger,
        permits=SqlPermitLedger(PermitLedger(SIGNER), engine),
        instruments=PAPER_INSTRUMENTS,
        store=SqlOrderStore(engine, account_id=ACCOUNT),
        clock=lambda: NOW,
    )
    await oms.recover()
    return oms, ledger


async def _fresh_db(directory: Path, name: str) -> AsyncEngine:
    engine = create_async_engine(f"sqlite+aiosqlite:///{directory / f'{name}.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(ledger_metadata.create_all)
    return engine


async def _reconciled(ledger: SqlLedger, venue: CountingVenue) -> bool:
    return reconcile_balances(await ledger.balances(), await venue.balances()) == []


async def _lost_response(report: DrillReport, directory: Path) -> None:
    engine = await _fresh_db(directory, "lost-response")
    try:
        venue = _venue(LostResponseVenue)
        oms, _ = await _boot(engine, venue)
        trade = _intent(1)
        order = await oms.submit(trade, SIGNER.issue(trade, now=NOW))
        report.expect("lost_response_marks_unknown", "UNKNOWN", order.status.value)
        restarted, ledger = await _boot(engine, venue)  # crash, then a new process
        resolved = await restarted.resolve_unknown(order.client_order_id)
        report.expect("lost_response_resolved_by_query", "FILLED", resolved.status.value)
        report.expect("lost_response_sent_once", 1, venue.submissions)
        report.expect("lost_response_ledger_reconciled", True, await _reconciled(ledger, venue))
        again = await restarted.submit(trade, SIGNER.issue(trade, now=NOW))
        report.expect(
            "same_intent_not_resent",
            f"{order.client_order_id}/1",
            f"{again.client_order_id}/{venue.submissions}",
        )
    finally:
        await engine.dispose()


async def _lost_request(report: DrillReport, directory: Path) -> None:
    engine = await _fresh_db(directory, "lost-request")
    try:
        venue = _venue(LostRequestVenue)
        oms, _ = await _boot(engine, venue)
        trade = _intent(2)
        order = await oms.submit(trade, SIGNER.issue(trade, now=NOW))
        restarted, ledger = await _boot(engine, venue)
        resolved = await restarted.resolve_unknown(order.client_order_id)
        report.expect("lost_request_rejected_after_query", "REJECTED", resolved.status.value)
        usdt = (await ledger.balances())["USDT"]
        report.expect(
            "lost_request_hold_released",
            True,
            usdt.reserved == 0 and usdt.available == OPENING["USDT"],
        )
    finally:
        await engine.dispose()


async def _crash_before_submit(report: DrillReport, directory: Path) -> None:
    engine = await _fresh_db(directory, "crash-before-submit")
    try:
        venue = _venue()
        oms, ledger = await _boot(engine, venue)
        trade = _intent(3)
        order = oms._new_order(trade, PAPER_INSTRUMENTS[BTC_ID])  # noqa: SLF001 - simulate the crash point
        await SqlOrderStore(engine, account_id=ACCOUNT).save(order)
        await ledger.post([oms._reservation(order, NOW)])  # noqa: SLF001
        restarted, ledger = await _boot(engine, venue)
        recovered = restarted.get(order.client_order_id)
        report.expect(
            "crash_before_submit_rejected",
            "REJECTED/interrupted before submit",
            f"{recovered.status.value}/{recovered.reason}" if recovered else "missing",
        )
        usdt = (await ledger.balances())["USDT"]
        report.expect(
            "crash_before_submit_funds_released",
            True,
            usdt.reserved == 0 and usdt.available == OPENING["USDT"],
        )
        report.expect("crash_before_submit_never_sent", 0, venue.submissions)
    finally:
        await engine.dispose()


async def _resting_order(report: DrillReport, directory: Path) -> None:
    engine = await _fresh_db(directory, "resting")
    try:
        venue = _venue()
        oms, _ = await _boot(engine, venue)
        trade = _intent(4, limit_price=Decimal("60000.00"))
        order = await oms.submit(trade, SIGNER.issue(trade, now=NOW))
        report.expect("resting_order_acknowledged", "ACKNOWLEDGED", order.status.value)
        restarted, _ = await _boot(engine, venue)
        recovered = restarted.get(order.client_order_id)
        report.expect(
            "resting_order_unknown_after_restart",
            "UNKNOWN",
            recovered.status.value if recovered else "missing",
        )
        resolved = await restarted.resolve_unknown(order.client_order_id)
        report.expect("resting_order_resolved_from_venue", "ACKNOWLEDGED", resolved.status.value)
    finally:
        await engine.dispose()


async def _spent_permit(report: DrillReport, directory: Path) -> None:
    engine = await _fresh_db(directory, "spent-permit")
    try:
        venue = _venue()
        oms, _ = await _boot(engine, venue)
        trade = _intent(5)
        permit = SIGNER.issue(trade, now=NOW)
        await oms.submit(trade, permit)
        restarted, _ = await _boot(engine, venue)
        other = _intent(6)
        try:
            await restarted.submit(other, permit)
            outcome = "accepted"
        except OrderRejected as exc:
            outcome = exc.code
        report.expect("spent_permit_refused_after_restart", "permit_intent_mismatch", outcome)
        replay = _intent(5)
        same = await restarted.submit(replay, permit)
        report.expect(
            "spent_permit_same_intent_returns_order",
            "FILLED/1",
            f"{same.status.value}/{venue.submissions}",
        )
    finally:
        await engine.dispose()


async def run(seed: int = 0) -> DrillReport:
    """The cases are fixed; `seed` is recorded so every drill reference has the same shape."""

    report = DrillReport(kind="RECOVERY_DRILL", seed=seed)
    with tempfile.TemporaryDirectory(prefix="proto-recovery-drill-") as tmp:
        directory = Path(tmp)
        for case in (
            _lost_response,
            _lost_request,
            _crash_before_submit,
            _resting_order,
            _spent_permit,
        ):
            try:
                await case(report, directory)
            except Exception as exc:  # recorded as a failed case
                report.guard(case.__name__.lstrip("_"), exc)
    return report
