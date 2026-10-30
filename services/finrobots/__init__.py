"""PROTO FinRobots: specialist analysts, their registry, council and orchestrator.

Advisory only. No FinRobot holds order authority; trading stays signal -> agents -> Risk
Engine permit -> OMS. Contract: docs/finrobots/contract.md.
"""

from .contracts import (
    CONTRACT_VERSION,
    DataProvenance,
    Evidence,
    FinRobotId,
    FinRobotReport,
    Stance,
)
from .council import CouncilResult, ScopeVerdict, Verdict, decide
from .orchestrator import CycleRecord, FinRobotOrchestrator
from .registry import SPECS, CycleContext, FinRobot, FinRobotRegistry, FinRobotSpec, RegistryError

__all__ = [
    "CONTRACT_VERSION",
    "SPECS",
    "CouncilResult",
    "CycleContext",
    "CycleRecord",
    "DataProvenance",
    "Evidence",
    "FinRobot",
    "FinRobotId",
    "FinRobotOrchestrator",
    "FinRobotRegistry",
    "FinRobotReport",
    "FinRobotSpec",
    "RegistryError",
    "ScopeVerdict",
    "Stance",
    "Verdict",
    "decide",
]
