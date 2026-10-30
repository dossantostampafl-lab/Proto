"""Three evidence-bound advisory FinRobots. No jobs, orders, permits or I/O."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import ValidationError

from services.finrobots.contracts import DataProvenance, Evidence, FinRobotId, FinRobotReport, Stance
from services.finrobots.registry import CycleContext
from services.quant.evidence import QuantEvidence, _payload

_SOURCE = "quant.evidence"
_MAX_READ_AGE = timedelta(minutes=10)
_PROVENANCES = {"PAPER", "SIMULATED", "REPLAY", "MIXED"}


def _records(context: CycleContext) -> list[tuple[QuantEvidence, str]]:
    """Accept only hash-verified rows from a fresh, successful read-only snapshot.

    The record's historical observation time is distinct from snapshot freshness.
    A replay may be valid research without being current trading evidence.
    """
    meta = context.snapshot.get("_sources")
    sources = meta.get("sources") if isinstance(meta, Mapping) else None
    source = sources.get(_SOURCE) if isinstance(sources, Mapping) else None
    data = context.snapshot.get(_SOURCE)
    if (
        not isinstance(source, Mapping)
        or source.get("status") != "OK"
        or source.get("provenance") not in _PROVENANCES
        or not isinstance(data, Mapping)
        or data.get("contract_version") != "quant-evidence/1"
        or not isinstance(data.get("records"), list)
    ):
        return []
    try:
        read_at = datetime.fromisoformat(source["read_at"])
        now = datetime.now(UTC)
        if (
            read_at.tzinfo is None
            or read_at.utcoffset() is None
            or not timedelta(0) <= now - read_at <= _MAX_READ_AGE
        ):
            return []
    except (KeyError, TypeError, ValueError, OverflowError):
        return []

    accepted: list[tuple[QuantEvidence, str]] = []
    for row in data["records"][:100]:
        if not isinstance(row, dict) or not isinstance(row.get("evidence_id"), str):
            continue
        payload = {key: value for key, value in row.items() if key != "evidence_id"}
        try:
            envelope = QuantEvidence.model_validate(payload)
            identity, canonical = _payload(envelope)
        except (ValidationError, ValueError, TypeError):
            continue
        if (
            identity != row["evidence_id"]
            or canonical != payload
            or envelope.instrument_id not in context.instrument_ids
            or envelope.observed_at > now
            or envelope.computed_at > now
            or (
                source["provenance"] != "MIXED"
                and envelope.input_provenance != source["provenance"]
            )
        ):
            continue
        accepted.append((envelope, identity))

    accepted.sort(key=lambda item: (item[0].observed_at, item[1]), reverse=True)
    latest: dict[str, tuple[QuantEvidence, str]] = {}
    for envelope, identity in accepted:
        latest.setdefault(envelope.instrument_id, (envelope, identity))
    return list(latest.values())


def _evidence(envelope: QuantEvidence, identity: str) -> Evidence:
    return Evidence(
        source=_SOURCE,
        provenance=DataProvenance(envelope.input_provenance),
        observed_at=envelope.observed_at,
        summary="Hash-verified persisted quantitative pipeline result; not an execution authorization.",
        metrics={
            "evidence_id": identity,
            "mode": envelope.mode,
            "model_version": envelope.model_version,
            "feature_version": envelope.feature_version,
            "net_edge": envelope.result["net_edge"],
            "risk_adjusted_ev": envelope.result["risk_adjusted_ev"],
        },
    )


class _QuantAdvisory:
    robot_id: FinRobotId
    implementation: str
    unavailable_headline: str

    def _abstain(
        self,
        context: CycleContext,
        *,
        instrument_id: str | None = None,
        headline: str | None = None,
        evidence: tuple[Evidence, ...] = (),
    ) -> FinRobotReport:
        return FinRobotReport(
            robot_id=self.robot_id,
            implementation=self.implementation,
            cycle_id=context.cycle_id,
            instrument_id=instrument_id,
            stance=Stance.ABSTAIN,
            headline=headline or self.unavailable_headline,
            evidence=evidence,
        )

    async def analyze(self, context: CycleContext) -> FinRobotReport | list[FinRobotReport]:
        records = _records(context)
        if not records:
            return self._abstain(context)
        return [self._review(context, envelope, identity) for envelope, identity in records]

    def _review(
        self, context: CycleContext, envelope: QuantEvidence, identity: str
    ) -> FinRobotReport:
        raise NotImplementedError


class QuantResearch(_QuantAdvisory):
    robot_id = FinRobotId.QUANT_RESEARCH
    implementation = "quant-research@1.0.0"
    unavailable_headline = "No verified quantitative evidence for active instruments"

    def _review(
        self, context: CycleContext, envelope: QuantEvidence, identity: str
    ) -> FinRobotReport:
        item = _evidence(envelope, identity)
        # Historical replay and simulated estimates never vote on current risk.
        # A recent shadow observation can warn about an already recorded bad edge.
        recent_paper = (
            envelope.mode == "SHADOW"
            and envelope.input_provenance == "PAPER"
            and timedelta(0) <= datetime.now(UTC) - envelope.observed_at <= _MAX_READ_AGE
        )
        unfavorable = (
            Decimal(envelope.result["net_edge"]) <= 0
            or Decimal(envelope.result["risk_adjusted_ev"]) <= 0
        )
        if recent_paper and unfavorable:
            return FinRobotReport(
                robot_id=self.robot_id,
                implementation=self.implementation,
                cycle_id=context.cycle_id,
                instrument_id=envelope.instrument_id,
                stance=Stance.CAUTION,
                headline="Recorded paper quant estimate has non-positive net edge or risk-adjusted EV",
                evidence=(item,),
            )
        return self._abstain(
            context,
            instrument_id=envelope.instrument_id,
            headline="Quant result recorded; no independently validated trading signal",
            evidence=(item,),
        )


class StrategyResearch(_QuantAdvisory):
    robot_id = FinRobotId.STRATEGY_RESEARCH
    implementation = "strategy-research@1.0.0"
    unavailable_headline = "No verified strategy backtest or validation evidence"

    def _review(
        self, context: CycleContext, envelope: QuantEvidence, identity: str
    ) -> FinRobotReport:
        # QuantEvidence has no strategy ID, backtest, out-of-sample or slippage distribution.
        return self._abstain(
            context,
            instrument_id=envelope.instrument_id,
            headline="Quant estimate is not a strategy backtest; strategy view unavailable",
            evidence=(_evidence(envelope, identity),),
        )


class ModelValidator(_QuantAdvisory):
    robot_id = FinRobotId.MODEL_VALIDATOR
    implementation = "model-validator@1.0.0"
    unavailable_headline = "No verified model validation or holdout evidence"

    def _review(
        self, context: CycleContext, envelope: QuantEvidence, identity: str
    ) -> FinRobotReport:
        # The input samples prove neither holdout isolation nor calibrated model quality.
        # Never issue a veto (or approval) without persisted validation results.
        return self._abstain(
            context,
            instrument_id=envelope.instrument_id,
            headline="Quant inputs recorded; holdout and data-snooping validation unavailable",
            evidence=(_evidence(envelope, identity),),
        )
