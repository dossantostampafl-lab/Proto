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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.execution import ReleaseStage, TradeIntent
from services.execution.constitution import CANARY_CAPITAL
from services.promotion import (
    EVIDENCE_MAX_AGE,
    MIN_PAPER_PERIOD,
    REQUIRED_EVIDENCE,
    STAGE_ORDER,
    EvidenceKind,
    PromotionRefused,
    PromotionRegistry,
    Scope,
)

from .app_state import persistence_engine
from .event_state import JournalUnavailable, record_operational_event
from .settings import settings

logger = logging.getLogger("proto.promotion")

router = APIRouter(prefix="/v1/promotion", tags=["promotion"])

_BOUNDARY = {"financial_connectivity": False, "real_money_execution": False}
_registry: PromotionRegistry | None = None


def registry() -> PromotionRegistry:
    global _registry
    if _registry is None:
        _registry = PromotionRegistry(persistence_engine)
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
        "operator_only": ["CANARY", "LIVE"],
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
