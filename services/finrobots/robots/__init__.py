"""Read-only FinRobot implementations; no execution authority."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from math import isfinite

from services.finrobots.contracts import (
    DataProvenance,
    Evidence,
    FinRobotId,
    FinRobotReport,
    Stance,
)
from services.finrobots.registry import CycleContext, FinRobot


class RiskGuardian:
    robot_id = FinRobotId.RISK_GUARDIAN
    implementation = "risk-guardian@1.0.0"

    async def analyze(self, context: CycleContext) -> FinRobotReport | list[FinRobotReport]:
        status = context.snapshot.get("risk.status")
        controls = status.get("controls") if isinstance(status, Mapping) else None
        source = status.get("account_source") if isinstance(status, Mapping) else None
        provenance = (
            DataProvenance(source) if source in ("PAPER", "TESTNET") else DataProvenance.LIVE
        )
        active = False
        if isinstance(controls, Mapping):
            global_kill = controls.get("global_kill")
            runtime_kill = controls.get("runtime_kill_switch")
            lockdown = controls.get("lockdown")
            active = (
                global_kill is True
                or runtime_kill == "TRIGGERED"
                or (isinstance(lockdown, str) and bool(lockdown))
            )
        evidence = (
            (
                Evidence(
                    source="risk.status",
                    provenance=provenance,
                    observed_at=context.started_at,
                    summary="Global runtime kill switch or lockdown is active; not a market quote.",
                ),
            )
            if active
            else ()
        )
        if not active and isinstance(controls, Mapping):
            killed = controls.get("killed_venues")
            if isinstance(killed, list):
                venues = {value for value in killed if isinstance(value, str) and value}
                reports = [
                    FinRobotReport(
                        robot_id=self.robot_id,
                        implementation=self.implementation,
                        cycle_id=context.cycle_id,
                        instrument_id=instrument,
                        stance=Stance.VETO,
                        headline="Venue kill switch active",
                        evidence=(
                            Evidence(
                                source="risk.status",
                                provenance=provenance,
                                observed_at=context.started_at,
                                summary="The instrument's venue has an active kill switch.",
                            ),
                        ),
                    )
                    for instrument in context.instrument_ids
                    if ":" in instrument and instrument.split(":", 1)[0] in venues
                ]
                if reports:
                    return reports
        return FinRobotReport(
            robot_id=self.robot_id,
            implementation=self.implementation,
            cycle_id=context.cycle_id,
            stance=Stance.VETO if active else Stance.ABSTAIN,
            headline=(
                "Active global risk control" if active else "Insufficient evidence for a risk view"
            ),
            evidence=evidence,
        )


class MarketIntelligence:
    robot_id = FinRobotId.MARKET_INTELLIGENCE
    implementation = "market-intelligence@1.0.0"

    async def analyze(self, context: CycleContext) -> FinRobotReport | list[FinRobotReport]:
        snapshot = context.snapshot
        meta = snapshot.get("_sources")
        sources = meta.get("sources") if isinstance(meta, Mapping) else None
        quotes = snapshot.get("market.quotes")
        risk = snapshot.get("risk.status")
        policy = risk.get("policy") if isinstance(risk, Mapping) else None
        max_age = policy.get("max_quote_age_seconds") if isinstance(policy, Mapping) else None
        quote_source = sources.get("market.quotes") if isinstance(sources, Mapping) else None
        risk_source = sources.get("risk.status") if isinstance(sources, Mapping) else None
        reports = []
        if (
            isinstance(quotes, Mapping)
            and isinstance(quote_source, Mapping)
            and quote_source.get("status") == "OK"
            and quote_source.get("provenance") == "LIVE"
            and isinstance(risk_source, Mapping)
            and risk_source.get("status") == "OK"
            and type(max_age) in (int, float)
            and isfinite(max_age)
            and max_age > 0
        ):
            now = datetime.now(UTC)
            for instrument in context.instrument_ids:
                quote = quotes.get(instrument)
                timestamp = quote.get("observed_at") if isinstance(quote, Mapping) else None
                if not isinstance(timestamp, str):
                    continue
                try:
                    observed = datetime.fromisoformat(timestamp)
                    if observed.tzinfo is None or observed.utcoffset() is None:
                        continue
                    age = (now - observed).total_seconds()
                except (ValueError, OverflowError):
                    continue
                if age <= max_age:
                    continue
                reports.append(
                    FinRobotReport(
                        robot_id=self.robot_id,
                        implementation=self.implementation,
                        cycle_id=context.cycle_id,
                        instrument_id=instrument,
                        stance=Stance.CAUTION,
                        headline="Market quote is stale",
                        evidence=(
                            Evidence(
                                source="market.quotes",
                                provenance=DataProvenance.STALE,
                                observed_at=observed,
                                summary="Quote age exceeds the Risk Engine's configured maximum.",
                            ),
                        ),
                    )
                )
        if reports:
            return reports
        return FinRobotReport(
            robot_id=self.robot_id,
            implementation=self.implementation,
            cycle_id=context.cycle_id,
            stance=Stance.ABSTAIN,
            headline="No stale quote established from available evidence",
        )


class ExecutionSupervisor:
    robot_id = FinRobotId.EXECUTION_SUPERVISOR
    implementation = "execution-supervisor@1.0.0"

    async def analyze(self, context: CycleContext) -> FinRobotReport | list[FinRobotReport]:
        snapshot = context.snapshot
        metadata = snapshot.get("_sources")
        sources = metadata.get("sources") if isinstance(metadata, Mapping) else None
        source = sources.get("oms.orders") if isinstance(sources, Mapping) else None
        data = snapshot.get("oms.orders")
        orders = data.get("orders") if isinstance(data, Mapping) else None
        reports = []
        if (
            isinstance(source, Mapping)
            and source.get("status") == "OK"
            and source.get("provenance") in ("PAPER", "TESTNET")
            and isinstance(source.get("read_at"), str)
            and isinstance(orders, list)
        ):
            try:
                read_at = datetime.fromisoformat(source["read_at"])
                now = datetime.now(UTC)
                valid_time = (
                    read_at.tzinfo is not None
                    and read_at.utcoffset() is not None
                    and timedelta(0) <= now - read_at <= timedelta(minutes=10)
                )
            except (ValueError, OverflowError):
                valid_time = False
            if valid_time:
                instruments = {
                    order.get("instrument_id")
                    for order in orders
                    if isinstance(order, Mapping)
                    and order.get("status") == "UNKNOWN"
                    and isinstance(order.get("instrument_id"), str)
                    and order.get("instrument_id") in context.instrument_ids
                }
                for instrument in sorted(instruments):
                    reports.append(
                        FinRobotReport(
                            robot_id=self.robot_id,
                            implementation=self.implementation,
                            cycle_id=context.cycle_id,
                            instrument_id=instrument,
                            stance=Stance.CAUTION,
                            headline="Unresolved order observed",
                            evidence=(
                                Evidence(
                                    source="oms.orders",
                                    provenance=DataProvenance(source["provenance"]),
                                    observed_at=read_at,
                                summary="An UNKNOWN order appeared in the limited OMS snapshot.",
                                ),
                            ),
                        )
                    )
        if reports:
            return reports
        return FinRobotReport(
            robot_id=self.robot_id,
            implementation=self.implementation,
            cycle_id=context.cycle_id,
            stance=Stance.ABSTAIN,
            headline="Insufficient evidence to assess order execution",
        )


class AuditRecovery:
    robot_id = FinRobotId.AUDIT_RECOVERY
    implementation = "audit-recovery@1.0.0"

    async def analyze(self, context: CycleContext) -> FinRobotReport:
        status = context.snapshot.get("agents.status")
        failures = status.get("journal_failures") if isinstance(status, Mapping) else None
        alert = type(failures) is int and failures > 0 and status.get("stage") == "PAPER"
        evidence = (
            (
                Evidence(
                    source="agents.status",
                    provenance=DataProvenance.PAPER,
                    observed_at=context.started_at,
                    summary="Agent decision journal failures recorded in this process since reset.",
                    metrics={"journal_failures": str(failures)},
                ),
            )
            if alert
            else ()
        )
        return FinRobotReport(
            robot_id=self.robot_id,
            implementation=self.implementation,
            cycle_id=context.cycle_id,
            stance=Stance.CAUTION if alert else Stance.ABSTAIN,
            headline="Agent journal failures observed" if alert else "Insufficient audit evidence",
            evidence=evidence,
        )


def default_robots() -> list[FinRobot]:
    return [MarketIntelligence(), RiskGuardian(), ExecutionSupervisor(), AuditRecovery()]
