"""Promotion gates surface (Phase 6).

Scopes are account x venue x strategy x model version x credential. The Risk
Engine reads a scope's stage on every assessment; CANARY intents pass only for
a scope certified at CANARY, sized on at most R$500. Reconciliation
divergences and latched lockdowns are violations: they reset the clean paper
period and demote CANARY scopes automatically. Promotion into CANARY needs an
authenticated operator; LIVE cannot be certified yet.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.execution import ReleaseStage, TradeIntent
from services.execution.constitution import CANARY_CAPITAL
from services.promotion import (
    EVIDENCE_MAX_AGE,
    MIN_PAPER_PERIOD,
    OPERATOR_ONLY,
    REQUIRED_EVIDENCE,
    STAGE_ORDER,
    EvidenceKind,
    PromotionRefused,
    PromotionRegistry,
    Scope,
)

from . import control_state
from .event_state import JournalUnavailable, record_operational_event
from .settings import settings

logger = logging.getLogger("proto.promotion")

router = APIRouter(prefix="/v1/promotion", tags=["promotion"])

_BOUNDARY = {"financial_connectivity": False, "real_money_execution": False}
_registry: PromotionRegistry | None = None


def registry() -> PromotionRegistry:
    global _registry
    if _registry is None:
        # Durable on the control-plane store, so clean paper days and evidence accumulate
        # across deploys (production LIVE_MONITORING included).
        _registry = PromotionRegistry(control_state.current().engine)
    return _registry


def reset_promotion_state() -> None:
    global _registry
    _registry = None


def scope_of(intent: TradeIntent) -> Scope:
    """The scope an intent runs under with the current execution backend."""

    from .oms_surface import ACCOUNT_ID, EXECUTOR_ACCOUNT_ID, executor_mode

    executor = executor_mode()
    return Scope(
        account=EXECUTOR_ACCOUNT_ID if executor else ACCOUNT_ID,
        venue=intent.venue.value,
        strategy_id=intent.strategy_id,
        model_version=intent.model_version,
        credential_id="binance-testnet" if executor else "paper",
    )


async def stage_for(intent: TradeIntent) -> ReleaseStage:
    return await registry().stage(scope_of(intent))


async def _journal(event_type: str, payload: dict) -> None:
    try:
        await record_operational_event(
            event_type=event_type, source="promotion", payload={**payload, **_BOUNDARY}
        )
    except JournalUnavailable:
        logger.exception("%s was not journaled", event_type)


async def violation(*, venue: str, kind: str, detail: str) -> None:
    """Called by the risk and OMS surfaces. Never raises: safety paths must not fail here."""

    try:
        touched = await registry().violation(venue=venue, kind=kind, detail=detail)
    except Exception:
        logger.exception("promotion violation could not be recorded")
        return
    for record in touched:
        await _journal(
            "PROMOTION_VIOLATION",
            {"scope": record.scope.to_dict(), "kind": kind, "stage": record.stage.value},
        )


class ScopeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: str = Field(min_length=1, max_length=80)
    venue: str = Field(min_length=1, max_length=32)
    strategy_id: str = Field(min_length=1, max_length=120)
    model_version: str = Field(min_length=1, max_length=80)
    credential_id: str = Field(min_length=1, max_length=80)

    def scope(self) -> Scope:
        values = self.model_dump()
        if any("|" in v for v in values.values()):
            raise HTTPException(422, "scope fields cannot contain '|'")
        return Scope(**values)


class EvidenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ScopeBody
    kind: EvidenceKind
    reference: str = Field(min_length=3, max_length=300)
    passed: bool


class PromoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ScopeBody
    target: ReleaseStage


class DemoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ScopeBody
    reason: str = Field(min_length=3, max_length=200)


@router.get("/policy")
def policy() -> dict[str, object]:
    return {
        "stages": [s.value for s in STAGE_ORDER],
        "required_evidence": {
            stage.value: sorted(k.value for k in kinds)
            for stage, kinds in REQUIRED_EVIDENCE.items()
        },
        "canary_capital_brl": str(CANARY_CAPITAL),
        "min_paper_days": MIN_PAPER_PERIOD.days,
        "evidence_max_age_days": EVIDENCE_MAX_AGE.days,
        "operator_only": sorted(s.value for s in OPERATOR_ONLY),
        "live_certifiable": False,
        **_BOUNDARY,
    }


@router.get("/scopes")
async def scopes() -> dict[str, object]:
    return {"scopes": [r.to_dict() for r in await registry().records()], **_BOUNDARY}


@router.post("/evidence")
async def add_evidence(body: EvidenceBody) -> dict[str, object]:
    record = await registry().add_evidence(
        body.scope.scope(), body.kind, reference=body.reference, passed=body.passed, by="operator"
    )
    await _journal(
        "PROMOTION_EVIDENCE_RECORDED",
        {
            "scope": record.scope.to_dict(),
            "kind": body.kind.value,
            "passed": body.passed,
            "reference": body.reference,
        },
    )
    return {"scope": record.to_dict(), **_BOUNDARY}


class ScopeOnlyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ScopeBody


@router.post("/evidence/watchdog")
async def add_watchdog_evidence(body: ScopeOnlyBody) -> dict[str, object]:
    """Record WATCHDOG_ACTIVE from the watchdog's own heartbeats, never from a claim.

    It passes only while the watchdog is beating and has covered at least MIN_COVERAGE
    without a gap; otherwise a failed evidence is recorded with the measured coverage.
    """

    from .watchdog_surface import MIN_COVERAGE, state

    now = datetime.now(UTC)
    watch = state()
    covered = watch.coverage(now)
    passed = watch.active(now) and covered >= MIN_COVERAGE
    reference = (
        f"watchdog:{watch.watchdog_id or 'none'}:covered={covered.total_seconds() / 3600:.1f}h"
        f":since={watch.covered_since.isoformat() if watch.covered_since else 'never'}"
    )
    record = await registry().add_evidence(
        body.scope.scope(),
        EvidenceKind.WATCHDOG_ACTIVE,
        reference=reference,
        passed=passed,
        by="watchdog",
    )
    await _journal(
        "PROMOTION_EVIDENCE_RECORDED",
        {
            "scope": record.scope.to_dict(),
            "kind": EvidenceKind.WATCHDOG_ACTIVE.value,
            "passed": passed,
            "reference": reference,
        },
    )
    return {"scope": record.to_dict(), "passed": passed, "reference": reference, **_BOUNDARY}


class DrillBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ScopeBody
    kind: EvidenceKind
    seed: int = Field(default=0, ge=0, le=2**31 - 1)


@router.post("/evidence/drill")
async def add_drill_evidence(body: DrillBody) -> dict[str, object]:
    """Run a drill here, offline and away from production state, and record its outcome.

    The evidence reference carries the drill version, seed and digest, so anyone can re-run it
    with `python -m services.drills run <kind> --seed <seed>` and compare.
    """

    from services.drills import DRILLS, run_drill

    if body.kind not in DRILLS:
        raise HTTPException(422, f"{body.kind.value} is not a drill")
    report = await run_drill(body.kind, body.seed)
    record = await registry().add_evidence(
        body.scope.scope(), body.kind, reference=report.reference, passed=report.passed, by="drill"
    )
    await _journal(
        "PROMOTION_EVIDENCE_RECORDED",
        {
            "scope": record.scope.to_dict(),
            "kind": body.kind.value,
            "passed": report.passed,
            "reference": report.reference,
        },
    )
    return {
        "scope": record.to_dict(),
        "drill": {
            **{k: v for k, v in report.to_dict().items() if k != "cases"},
            "cases": len(report.cases),
            "failures": [c.name for c in report.failures],
        },
        **_BOUNDARY,
    }


@router.post("/promote")
async def promote(body: PromoteBody) -> dict[str, object]:
    # The operator middleware has authenticated this request only when a token is configured.
    operator = settings.operator_api_token is not None
    try:
        record = await registry().promote(
            body.scope.scope(), body.target, operator=operator, by="operator"
        )
    except PromotionRefused as exc:
        raise HTTPException(409, {"refused": exc.reasons}) from exc
    await _journal(
        "PROMOTION_STAGE_CHANGED", {"scope": record.scope.to_dict(), "stage": record.stage.value}
    )
    return {"scope": record.to_dict(), **_BOUNDARY}


@router.post("/demote")
async def demote(body: DemoteBody) -> dict[str, object]:
    record = await registry().demote(body.scope.scope(), reason=body.reason, by="operator")
    await _journal(
        "PROMOTION_STAGE_CHANGED",
        {"scope": record.scope.to_dict(), "stage": record.stage.value, "reason": body.reason},
    )
    return {"scope": record.to_dict(), **_BOUNDARY}
