"""Canonical Phase 4 paper execution surface; no financial connectivity.

Only explicitly enabled paper automation may reach the OMS. Audit failures
prevent dispatch; a failure after a fill persistently halts the venue.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncEngine

from services.execution import RiskPermit, TradeIntent, VenueId
from services.execution.catalog import PAPER_INSTRUMENTS
from services.execution.constitution import REFERENCE_CAPITAL
from services.execution.oms import OrderRejected
from services.execution.paper_runtime import Phase4PaperRuntime
from services.risk.engine import AccountState
from services.risk.permits import PermitSigner

from .event_state import JournalUnavailable, record_operational_event
from .market_quotes import live_quote
from .settings import settings
from .simulation import SimulationConfig

router = APIRouter(prefix="/v1/execution", tags=["execution"])

_runtime: Phase4PaperRuntime | None = None


def current_runtime() -> Phase4PaperRuntime:
    if not settings.canonical_paper_oms_enabled or _runtime is None or not _runtime.running:
        raise OrderRejected("paper_runtime_unavailable")
    return _runtime


async def start(engine: AsyncEngine, signer: PermitSigner) -> None:
    global _runtime
    if not settings.canonical_paper_oms_enabled:
        return
    if _runtime is not None and _runtime.running:
        return
    instruments = tuple(
        item for item in PAPER_INSTRUMENTS.values()
        if item.venue is VenueId.BINANCE_SPOT and item.quote_asset == "BRL"
    )
    costs = SimulationConfig()
    paper = Phase4PaperRuntime(
        engine=engine,
        signer=signer,
        instruments=instruments,
        quotes=lambda instrument_id: live_quote(
            instrument_id, market_source=settings.live_market_source
        ),
        starting_balances={"BRL": REFERENCE_CAPITAL},
        fee_bps=Decimal(str(costs.fee_bps)),
        slippage_bps=Decimal(str(costs.base_slippage_bps)),
    )
    await paper.start(now=datetime.now(UTC))
    _runtime = paper


async def stop() -> None:
    global _runtime
    old = _runtime
    _runtime = None
    if old is not None:
        await old.stop()


async def risk_account(*, now: datetime) -> AccountState:
    return await current_runtime().risk_account(now=now)


async def execute_order(intent: TradeIntent, permit: RiskPermit, *, now: datetime):
    """Journal intention before a paper order, and outcome after it."""

    worker = current_runtime()
    order_id = intent.client_order_id(0)
    await record_operational_event(
        event_type="PAPER_ORDER_AUTHORIZED",
        source="canonical-paper-oms",
        idempotency_key=f"paper-authorized:{order_id}",
        payload={
            "intent_id": str(intent.intent_id),
            "intent_fingerprint": intent.fingerprint(),
            "client_order_id": order_id,
            "permit_id": str(permit.permit_id),
            "stage": intent.stage.value,
            "real_money_execution": False,
        },
    )
    report = await worker.execute_authorized(intent, permit, now=now)
    try:
        await record_operational_event(
            event_type="PAPER_ORDER_REPORT",
            source="canonical-paper-oms",
            idempotency_key=(
                f"paper-report:{order_id}:{report.status.value}:{report.filled_quantity}"
            ),
            payload={
                "client_order_id": report.client_order_id,
                "status": report.status.value,
                "filled_quantity": str(report.filled_quantity),
                "fee": str(report.fee),
                "financial_connectivity": False,
                "real_money_execution": False,
            },
        )
    except JournalUnavailable:
        await worker.oms._halt("paper_report_audit_unavailable", now)
        raise
    return report


@router.get("/status")
def execution_status() -> dict[str, object]:
    runtime = _runtime
    return {
        "enabled": settings.canonical_paper_oms_enabled,
        "running": bool(runtime and runtime.running),
        "reconciliation_failure": runtime.background_failure if runtime else None,
        "venue": runtime.adapter.venue.value if runtime else None,
        "stage": "PAPER",
        "account_source": "durable_execution_ledger" if runtime else "unavailable",
        "financial_connectivity": False,
        "real_money_execution": False,
    }
