"""Decision council: a deterministic fold of the specialists' reports, per instrument.

The council never votes a trade in. Its verdict only ranks how much *less* risk the evidence
supports: ``BLOCK_NEW_RISK`` > ``REDUCE_RISK`` > ``CONSISTENT`` / ``NO_CONSENSUS``. Nothing
here changes a limit, issues a permit or reaches the OMS; the Risk Engine stays the only
authority, and agent or model consensus never replaces it (AGENTS.md).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from .contracts import Evidence, FinRobotReport, Stance
from .registry import SPECS

SYSTEM_SCOPE = "SYSTEM"
DEFAULT_MAX_REPORT_AGE = timedelta(minutes=10)
DEFAULT_SUPPORT_QUORUM = 2


class Verdict(StrEnum):
    BLOCK_NEW_RISK = "BLOCK_NEW_RISK"
    REDUCE_RISK = "REDUCE_RISK"
    CONSISTENT = "CONSISTENT"
    NO_CONSENSUS = "NO_CONSENSUS"


@dataclass(frozen=True, slots=True)
class ScopeVerdict:
    scope: str
    verdict: Verdict
    reasons: tuple[str, ...]
    votes: dict[str, str]
    evidence: tuple[Evidence, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "verdict": self.verdict.value,
            "reasons": list(self.reasons),
            "votes": dict(self.votes),
            "evidence": [e.model_dump(mode="json") for e in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class CouncilResult:
    decided_at: datetime
    scopes: tuple[ScopeVerdict, ...]
    excluded: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "decided_at": self.decided_at.isoformat(),
            "scopes": [s.to_dict() for s in self.scopes],
            "excluded": list(self.excluded),
            "advisory_only": True,
            "order_authority": False,
            "financial_connectivity": False,
            "real_money_execution": False,
        }


def _shared_evidence(reports: Iterable[FinRobotReport]) -> tuple[Evidence, ...]:
    seen: set[tuple[str, str, str, str, tuple[tuple[str, str], ...]]] = set()
    out: list[Evidence] = []
    for report in reports:
        for item in report.evidence:
            key = (
                item.source,
                item.provenance.value,
                item.observed_at.isoformat(),
                item.summary,
                tuple(sorted(item.metrics.items())),
            )
            if key not in seen:
                seen.add(key)
                out.append(item)
    return tuple(out[:32])


def _effective_stance(report: FinRobotReport) -> Stance:
    if report.stance is Stance.VETO and not SPECS[report.robot_id].may_veto:
        return Stance.CAUTION
    return report.stance


_EVIDENCE_PRIORITY = {Stance.VETO: 0, Stance.CAUTION: 1, Stance.SUPPORT: 2, Stance.ABSTAIN: 3}


def decide(
    reports: Iterable[FinRobotReport],
    *,
    now: datetime | None = None,
    max_report_age: timedelta = DEFAULT_MAX_REPORT_AGE,
    support_quorum: int = DEFAULT_SUPPORT_QUORUM,
) -> CouncilResult:
    now = now or datetime.now(UTC)
    excluded: list[str] = []
    by_scope: dict[str, dict[str, FinRobotReport]] = {}
    for report in reports:
        produced = report.produced_at.astimezone(UTC)
        if now - produced > max_report_age:
            excluded.append(f"{report.robot_id.value}: stale report")
            continue
        if produced - now > timedelta(seconds=5):
            excluded.append(f"{report.robot_id.value}: report dated in the future")
            continue
        scope = report.instrument_id or SYSTEM_SCOPE
        # One vote per specialist per scope: the latest report wins.
        current = by_scope.setdefault(scope, {}).get(report.robot_id.value)
        if current is None or current.produced_at <= report.produced_at:
            by_scope[scope][report.robot_id.value] = report

    # A system-wide veto (e.g. a kill switch seen by the Risk Guardian) applies to every scope.
    system_reports = by_scope.get(SYSTEM_SCOPE, {})
    verdicts: list[ScopeVerdict] = []
    for scope in sorted(by_scope):
        own = by_scope[scope]
        inherited = {} if scope == SYSTEM_SCOPE else system_reports
        selected = dict(own)
        for robot, inherited_report in inherited.items():
            own_report = selected.get(robot)
            inherited_stance = _effective_stance(inherited_report)
            own_stance = _effective_stance(own_report) if own_report is not None else None
            if (
                own_report is None
                or inherited_stance is Stance.VETO
                or (
                    inherited_stance is Stance.CAUTION
                    and own_stance not in (Stance.VETO, Stance.CAUTION)
                )
            ):
                selected[robot] = inherited_report
        votes: dict[str, str] = {}
        reasons: list[str] = []
        vetoed = cautioned = False
        support = 0
        for robot, report in sorted(selected.items()):
            stance = report.stance
            if stance is Stance.VETO and not SPECS[report.robot_id].may_veto:
                reasons.append(f"{robot}: veto not in its mandate, counted as caution")
                stance = Stance.CAUTION
            votes[robot] = stance.value
            if stance is Stance.VETO:
                vetoed = True
                reasons.append(f"{robot}: {report.headline}")
            elif stance is Stance.CAUTION:
                cautioned = True
                reasons.append(f"{robot}: {report.headline}")
            elif stance is Stance.SUPPORT and own.get(robot) is report:
                support += 1
        if vetoed:
            verdict = Verdict.BLOCK_NEW_RISK
        elif cautioned:
            verdict = Verdict.REDUCE_RISK
        elif support >= support_quorum:
            verdict = Verdict.CONSISTENT
            reasons.append(f"{support} specialists support with evidence")
        else:
            verdict = Verdict.NO_CONSENSUS
            reasons.append(f"support {support} below quorum {support_quorum}")
        verdicts.append(
            ScopeVerdict(
                scope=scope,
                verdict=verdict,
                reasons=tuple(reasons),
                votes=votes,
                evidence=_shared_evidence(
                    sorted(
                        (*selected.values(), *inherited.values(), *own.values()),
                        key=lambda item: _EVIDENCE_PRIORITY[_effective_stance(item)],
                    )
                ),
            )
        )
    return CouncilResult(decided_at=now, scopes=tuple(verdicts), excluded=tuple(excluded))
