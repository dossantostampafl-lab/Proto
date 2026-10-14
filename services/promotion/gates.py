"""Promotion gates, per scope (account x venue x strategy x model version x credential).

    RESEARCH -> REPLAY -> SHADOW -> PAPER -> CANARY -> LIVE

A scope moves one stage at a time and only with the evidence its next stage
requires. Any violation demotes it automatically to a safer stage, and
automation can only ever demote:

- promotion into CANARY needs an operator, a full paper record and fresh
  passing adversarial, recovery, rollback and watchdog evidence;
- LIVE is not certifiable yet;
- the canary capital (R$500) and the paper minimum (7 days) are the owner's
  decisions recorded in docs/plans/2026-10-13-tradingview-agent-trading.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from services.execution import ReleaseStage

STAGE_ORDER = (
    ReleaseStage.RESEARCH,
    ReleaseStage.REPLAY,
    ReleaseStage.SHADOW,
    ReleaseStage.PAPER,
    ReleaseStage.CANARY,
    ReleaseStage.LIVE,
)

MIN_PAPER_PERIOD = timedelta(days=7)
EVIDENCE_MAX_AGE = timedelta(days=14)


class EvidenceKind(StrEnum):
    REPLAY_REPRODUCIBLE = "REPLAY_REPRODUCIBLE"
    SHADOW_COMPARISON = "SHADOW_COMPARISON"
    ADVERSARIAL_SUITE = "ADVERSARIAL_SUITE"
    RECOVERY_DRILL = "RECOVERY_DRILL"
    ROLLBACK_DRILL = "ROLLBACK_DRILL"
    WATCHDOG_ACTIVE = "WATCHDOG_ACTIVE"


# Evidence needed to ENTER each stage (gathered while in the stage before it).
REQUIRED_EVIDENCE: dict[ReleaseStage, frozenset[EvidenceKind]] = {
    ReleaseStage.REPLAY: frozenset(),
    ReleaseStage.SHADOW: frozenset({EvidenceKind.REPLAY_REPRODUCIBLE}),
    ReleaseStage.PAPER: frozenset({EvidenceKind.SHADOW_COMPARISON}),
    ReleaseStage.CANARY: frozenset(
        {
            EvidenceKind.ADVERSARIAL_SUITE,
            EvidenceKind.RECOVERY_DRILL,
            EvidenceKind.ROLLBACK_DRILL,
            EvidenceKind.WATCHDOG_ACTIVE,
        }
    ),
}
OPERATOR_ONLY = frozenset({ReleaseStage.CANARY, ReleaseStage.LIVE})
# Where a violation sends a scope.
DEMOTION = {
    ReleaseStage.LIVE: ReleaseStage.CANARY,
    ReleaseStage.CANARY: ReleaseStage.PAPER,
}


class PromotionRefused(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass(frozen=True)
class Scope:
    account: str
    venue: str
    strategy_id: str
    model_version: str
    credential_id: str

    @property
    def key(self) -> str:
        return "|".join(
            (self.account, self.venue, self.strategy_id, self.model_version, self.credential_id)
        )

    @classmethod
    def from_key(cls, key: str) -> Scope:
        parts = key.split("|")
        if len(parts) != 5 or not all(parts):
            raise ValueError("scope key must have five non-empty parts")
        return cls(*parts)

    def to_dict(self) -> dict[str, str]:
        return {
            "account": self.account,
            "venue": self.venue,
            "strategy_id": self.strategy_id,
            "model_version": self.model_version,
            "credential_id": self.credential_id,
            "key": self.key,
        }


@dataclass(frozen=True)
class Evidence:
    kind: EvidenceKind
    reference: str  # CI run URL, commit SHA, drill id: where a reviewer can reproduce it
    passed: bool
    recorded_at: datetime
    recorded_by: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "reference": self.reference,
            "passed": self.passed,
            "recorded_at": self.recorded_at.isoformat(),
            "recorded_by": self.recorded_by,
        }


@dataclass
class ScopeRecord:
    scope: Scope
    stage: ReleaseStage
    stage_since: datetime
    clean_since: datetime  # start of the current violation-free period
    evidence: list[Evidence] = field(default_factory=list)
    violations: list[dict[str, str]] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope.to_dict(),
            "stage": self.stage.value,
            "stage_since": self.stage_since.isoformat(),
            "clean_since": self.clean_since.isoformat(),
            "evidence": [e.to_dict() for e in self.evidence[-50:]],
            "violations": self.violations[-50:],
            "history": self.history[-50:],
        }


def next_stage(stage: ReleaseStage) -> ReleaseStage | None:
    index = STAGE_ORDER.index(stage)
    return STAGE_ORDER[index + 1] if index + 1 < len(STAGE_ORDER) else None


def gate_check(
    record: ScopeRecord, target: ReleaseStage, *, now: datetime, operator: bool
) -> list[str]:
    """Every reason `target` is refused. Empty means the gate passes."""

    reasons: list[str] = []
    if target is not next_stage(record.stage):
        reasons.append(f"{record.stage.value} can only advance to the next stage")
        return reasons
    if target is ReleaseStage.LIVE:
        reasons.append("LIVE certification is not available yet")
    if target in OPERATOR_ONLY and not operator:
        reasons.append(f"{target.value} needs an operator")
    if target is ReleaseStage.CANARY:
        paper_for = now - max(record.stage_since, record.clean_since)
        if paper_for < MIN_PAPER_PERIOD:
            days = paper_for.total_seconds() / 86_400
            reasons.append(f"needs 7 clean days in PAPER (has {days:.1f})")
    for kind in sorted(REQUIRED_EVIDENCE.get(target, frozenset())):
        fresh = [
            e
            for e in record.evidence
            if e.kind is kind
            and e.recorded_at >= record.stage_since
            and now - e.recorded_at <= EVIDENCE_MAX_AGE
        ]
        if not fresh:
            reasons.append(f"missing {kind.value} evidence recorded in {record.stage.value}")
        elif not fresh[-1].passed:
            reasons.append(f"latest {kind.value} evidence failed")
    return reasons


def demotion_target(stage: ReleaseStage) -> ReleaseStage | None:
    return DEMOTION.get(stage)
