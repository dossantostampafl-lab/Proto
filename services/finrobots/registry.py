"""FinRobot Registry: the eight specialists, their mandates, and which engine is bound to each.

The registry is the single list of FinRobots. Each entry states its mandate, the PROTO
modules it must reuse (no parallel engines: see docs/finrobots/contract.md), whether it may
veto, and whether an implementation is bound. An unbound specialist is reported as
``UNBOUND`` and never as working: the council simply has no report from it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from .contracts import FinRobotId, FinRobotReport


@dataclass(frozen=True, slots=True)
class CycleContext:
    """Read-only inputs of one cycle. ``snapshot`` is gathered once by the orchestrator from
    PROTO's own read routes, so a specialist never touches a mutating surface."""

    cycle_id: UUID
    started_at: datetime
    instrument_ids: tuple[str, ...]
    snapshot: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class FinRobot(Protocol):
    robot_id: FinRobotId
    implementation: str

    async def analyze(self, context: CycleContext) -> FinRobotReport | list[FinRobotReport]: ...


@dataclass(frozen=True, slots=True)
class FinRobotSpec:
    robot_id: FinRobotId
    title: str
    mandate: str
    reuses: tuple[str, ...]
    may_veto: bool
    engine_owner: str = "feature/finrobots-codex"


SPECS: Mapping[FinRobotId, FinRobotSpec] = {
    spec.robot_id: spec
    for spec in (
        FinRobotSpec(
            FinRobotId.MARKET_INTELLIGENCE,
            "Market Intelligence",
            "Data freshness, regime and microstructure of the active instruments.",
            ("services.market_data", "services.features", "services.analytics"),
            may_veto=False,
        ),
        FinRobotSpec(
            FinRobotId.QUANT_RESEARCH,
            "Quant Research",
            "Probabilistic models and calibrated forecasts on recorded data.",
            ("services.quant", "services.hawkes", "services.research"),
            may_veto=False,
        ),
        FinRobotSpec(
            FinRobotId.STRATEGY_RESEARCH,
            "Strategy Research",
            "Backtests and validation of strategies with explicit costs.",
            ("services.research", "services.validation", "services.replay"),
            may_veto=False,
        ),
        FinRobotSpec(
            FinRobotId.PORTFOLIO_INTELLIGENCE,
            "Portfolio Intelligence",
            "Exposure, concentration and P&L attribution from the ledger.",
            ("services.portfolio", "services.oms.ledger", "services.agents.roster"),
            may_veto=False,
        ),
        FinRobotSpec(
            FinRobotId.MODEL_VALIDATOR,
            "Model Validator",
            "Holdout, data-snooping and calibration checks of every model in use.",
            ("services.validation", "apps.api.app.calibration_read"),
            may_veto=True,
        ),
        FinRobotSpec(
            FinRobotId.RISK_GUARDIAN,
            "Risk Guardian",
            "Watches the Risk Engine, kill switches and constitutional headroom.",
            ("services.risk", "services.execution.constitution"),
            may_veto=True,
        ),
        FinRobotSpec(
            FinRobotId.EXECUTION_SUPERVISOR,
            "Execution Supervisor",
            "Order lifecycle, UNKNOWN orders, slippage and venue health from the OMS.",
            ("services.oms", "services.promotion"),
            may_veto=True,
        ),
        FinRobotSpec(
            FinRobotId.AUDIT_RECOVERY,
            "Audit & Recovery",
            "Journal integrity, reconciliation divergences and recovery drills.",
            ("services.oms.reconciliation", "services.drills", "services.watchdog"),
            may_veto=True,
        ),
    )
}


class RegistryError(ValueError):
    pass


class FinRobotRegistry:
    """Binds at most one implementation to each specialist."""

    def __init__(self) -> None:
        self._bound: dict[FinRobotId, FinRobot] = {}

    def bind(self, robot: FinRobot) -> None:
        if not isinstance(robot, FinRobot):
            raise RegistryError("object does not implement the FinRobot protocol")
        robot_id = FinRobotId(robot.robot_id)
        if robot_id in self._bound:
            raise RegistryError(f"{robot_id.value} already has an implementation bound")
        self._bound[robot_id] = robot

    def unbind(self, robot_id: FinRobotId) -> None:
        self._bound.pop(robot_id, None)

    def bound(self) -> tuple[FinRobot, ...]:
        return tuple(self._bound[r] for r in FinRobotId if r in self._bound)

    def spec(self, robot_id: FinRobotId) -> FinRobotSpec:
        return SPECS[robot_id]

    def describe(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for robot_id in FinRobotId:
            spec = SPECS[robot_id]
            robot = self._bound.get(robot_id)
            out.append(
                {
                    "robot_id": robot_id.value,
                    "title": spec.title,
                    "mandate": spec.mandate,
                    "reuses": list(spec.reuses),
                    "may_veto": spec.may_veto,
                    "engine_owner": spec.engine_owner,
                    "status": "BOUND" if robot is not None else "UNBOUND",
                    "implementation": robot.implementation if robot is not None else None,
                    "order_authority": False,
                }
            )
        return out
