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

    async def analyze(self, context: CycleContext) -> FinRobotReport:
        status = context.snapshot.get("risk.status")
        controls = status.get("controls") if isinstance(status, Mapping) else None
        active = False
        if isinstance(controls, Mapping):
            global_kill = controls.get("global_kill")
            runtime_kill = controls.get("runtime_kill_switch")
            lockdown = controls.get("lockdown")
            complete = (
                type(global_kill) is bool
                and runtime_kill in ("ARMED", "TRIGGERED")
                and "lockdown" in controls
                and (lockdown is None or isinstance(lockdown, str) and bool(lockdown))
            )
            active = bool(complete and (global_kill or runtime_kill == "TRIGGERED" or lockdown))
        evidence = (
            Evidence(
                source="risk.status",
                provenance=DataProvenance.LIVE,
                observed_at=context.started_at,
                summary="An active global kill switch or lockdown was observed.",
            ),
        ) if active else ()
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


def default_robots() -> list[FinRobot]:
    return [RiskGuardian()]
