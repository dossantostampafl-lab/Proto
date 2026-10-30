"""Read-only FinRobot implementations; no execution authority."""

from collections.abc import Mapping

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
    return [RiskGuardian(), AuditRecovery()]
