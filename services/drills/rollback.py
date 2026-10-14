"""Rollback drill: a certified canary is pulled back, and every halt holds until an operator acts.

It walks a scope up to CANARY on a durable registry, injects the violations the system demotes on,
and checks that the scope falls back to PAPER, that CANARY intents are refused from then on, that
the demotion survives a restart, and that automation can neither re-promote nor release a halt.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import create_async_engine

from services.execution import OrderType, ReleaseStage, TradeIntent, VenueId
from services.execution.catalog import PAPER_INSTRUMENTS
from services.execution.constitution import CANARY_CAPITAL
from services.execution.venue import Quote
from services.promotion import EvidenceKind, PromotionRefused, PromotionRegistry, Scope
from services.risk import (
    AccountState,
    Controls,
    GovernorError,
    KillScope,
    PermitSigner,
    RiskEngine,
    RiskGovernor,
)

from .report import DrillReport

START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
SCOPE = Scope("drill-rollback", "BINANCE_SPOT", "drill-rollback", "drill", "paper")
QUOTE_AT = timedelta(seconds=1)
SIGNER = PermitSigner(b"drill-rollback-hmac-key-00000001")
CANARY_EVIDENCE = (
    EvidenceKind.ADVERSARIAL_SUITE,
    EvidenceKind.RECOVERY_DRILL,
    EvidenceKind.ROLLBACK_DRILL,
    EvidenceKind.WATCHDOG_ACTIVE,
)


class _Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


def _intent(n: int, now: datetime, **overrides) -> TradeIntent:
    values = {
        "intent_id": UUID(int=n),
        "instrument_id": "BINANCE_SPOT:BTCUSDT",
        "venue": VenueId.BINANCE_SPOT,
        "side": "BUY",
        "quantity": Decimal("0.0005"),
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal("62000.62"),
        "strategy_id": SCOPE.strategy_id,
        "model_version": SCOPE.model_version,
        "signal_ids": (UUID(int=10_000 + n),),
        "created_at": now,
    }
    values.update(overrides)
    return TradeIntent.model_validate(values)


def _account(equity: str = "10000") -> AccountState:
    return AccountState(
        equity=Decimal(equity),
        start_of_day_equity=Decimal("10000"),
        peak_equity=Decimal("10000"),
        gross_exposure=Decimal("0"),
        positions={},
    )


async def _to_canary(registry: PromotionRegistry, clock: _Clock) -> ReleaseStage:
    await registry.promote(SCOPE, ReleaseStage.REPLAY, operator=False, by="drill")
    await registry.add_evidence(
        SCOPE, EvidenceKind.REPLAY_REPRODUCIBLE, reference="drill", passed=True, by="drill"
    )
    await registry.promote(SCOPE, ReleaseStage.SHADOW, operator=False, by="drill")
    await registry.add_evidence(
        SCOPE, EvidenceKind.SHADOW_COMPARISON, reference="drill", passed=True, by="drill"
    )
    await registry.promote(SCOPE, ReleaseStage.PAPER, operator=False, by="drill")
    clock.now += timedelta(days=7, hours=1)
    for kind in CANARY_EVIDENCE:
        await registry.add_evidence(SCOPE, kind, reference="drill", passed=True, by="drill")
    record = await registry.promote(SCOPE, ReleaseStage.CANARY, operator=True, by="drill-operator")
    return record.stage


async def _promotion_rollback(report: DrillReport, directory: Path) -> None:
    clock = _Clock()
    engine = create_async_engine(f"sqlite+aiosqlite:///{directory / 'promotion.db'}")
    try:
        registry = PromotionRegistry(engine, clock=clock)
        report.expect("scope_reaches_canary", "CANARY", (await _to_canary(registry, clock)).value)

        risk = RiskEngine(instruments=PAPER_INSTRUMENTS, signer=SIGNER)
        now = clock.now
        quote = Quote(bid=Decimal("62000.00"), ask=Decimal("62000.20"), observed_at=now - QUOTE_AT)
        canary = _intent(1, now, stage=ReleaseStage.CANARY)
        before = risk.evaluate(
            canary,
            account=_account(),
            controls=Controls(),
            quote=quote,
            now=now,
            scope_stage=await registry.stage(SCOPE),
            canary_capital=CANARY_CAPITAL,
        )
        report.expect(
            "canary_intent_allowed_while_certified",
            "approved",
            "approved" if before.approved else ",".join(before.rejections),
        )

        clock.now += timedelta(hours=2)
        touched = await registry.violation(
            venue=SCOPE.venue, kind="RECONCILIATION_DIVERGENCE", detail="drill"
        )
        record = await registry.get(SCOPE)
        report.expect("violation_demotes_canary_to_paper", "PAPER", record.stage.value)
        report.expect("violation_restarts_clean_period", True, record.clean_since == clock.now)
        report.expect("violation_touches_the_venue_scope", 1, len(touched))

        after = risk.evaluate(
            _intent(2, now, stage=ReleaseStage.CANARY),
            account=_account(),
            controls=Controls(),
            quote=quote,
            now=now,
            scope_stage=await registry.stage(SCOPE),
            canary_capital=CANARY_CAPITAL,
        )
        report.expect(
            "canary_intent_refused_after_demotion", "stage_certified", ",".join(after.rejections)
        )

        try:
            await registry.promote(SCOPE, ReleaseStage.CANARY, operator=False, by="automation")
            outcome = "promoted"
        except PromotionRefused as exc:
            outcome = (
                "refused" if any("operator" in r for r in exc.reasons) else f"refused:{exc.reasons}"
            )
        report.expect("automation_cannot_repromote", "refused", outcome)

        restarted = PromotionRegistry(engine, clock=clock)
        report.expect("demotion_survives_restart", "PAPER", (await restarted.stage(SCOPE)).value)
        demoted = await restarted.demote(SCOPE, reason="drill rollback", by="operator")
        report.expect("operator_rolls_back_one_stage", "SHADOW", demoted.stage.value)
    finally:
        await engine.dispose()


def _halts(report: DrillReport) -> None:
    now = START
    quote = Quote(bid=Decimal("62000.00"), ask=Decimal("62000.20"), observed_at=now)
    governor = RiskGovernor(RiskEngine(instruments=PAPER_INSTRUMENTS, signer=SIGNER))

    def decide(n: int, equity: str = "10000") -> str:
        decision = governor.assess(_intent(n, now), account=_account(equity), quote=quote, now=now)
        return "approved" if decision.approved else ",".join(decision.rejections)

    report.expect("baseline_approved", "approved", decide(10))
    governor.trigger_kill(KillScope.GLOBAL)
    report.expect("kill_switch_blocks_new_risk", "global_kill_switch", decide(11))
    report.expect("kill_switch_still_holds_later", "global_kill_switch", decide(12))
    governor.release_kill(KillScope.GLOBAL)  # operator action
    report.expect("operator_release_restores_trading", "approved", decide(13))

    report.expect("breach_latches_lockdown", "no_lockdown", decide(14, equity="9890"))
    report.expect("lockdown_holds_after_recovery", "no_lockdown", decide(15))
    try:
        governor.release_lockdown(_account("9890"))
        outcome = "released"
    except GovernorError:
        outcome = "refused"
    report.expect("lockdown_release_refused_while_breached", "refused", outcome)
    governor.release_lockdown(_account())  # operator, after the account recovered
    report.expect("operator_release_after_recovery", "approved", decide(16))


async def run(seed: int = 0) -> DrillReport:
    """The cases are fixed; `seed` is recorded so every drill reference has the same shape."""

    report = DrillReport(kind="ROLLBACK_DRILL", seed=seed)
    with tempfile.TemporaryDirectory(prefix="proto-rollback-drill-") as tmp:
        try:
            await _promotion_rollback(report, Path(tmp))
        except Exception as exc:  # recorded as a failed case
            report.guard("promotion_rollback", exc)
    try:
        _halts(report)
    except Exception as exc:
        report.guard("halts", exc)
    return report
