"""Automated drills (Phase 6b): adversarial, recovery and rollback, with reproducible evidence.

Each drill runs the real control code (Risk Engine, permits, OMS, ledger, promotion registry,
governor) against offline fakes and temporary databases, never against production state, and
returns a report whose digest anyone can reproduce with the same version and seed:

    python -m services.drills run ADVERSARIAL_SUITE --seed 7 --out report.json
    python -m services.drills verify report.json
"""

from __future__ import annotations

from services.promotion import EvidenceKind

from . import adversarial, recovery, rollback
from .report import DRILL_VERSION, Case, DrillReport

DRILLS = (EvidenceKind.ADVERSARIAL_SUITE, EvidenceKind.RECOVERY_DRILL, EvidenceKind.ROLLBACK_DRILL)


async def run_drill(kind: EvidenceKind, seed: int = 0) -> DrillReport:
    if kind is EvidenceKind.ADVERSARIAL_SUITE:
        return adversarial.run(seed)
    if kind is EvidenceKind.RECOVERY_DRILL:
        return await recovery.run(seed)
    if kind is EvidenceKind.ROLLBACK_DRILL:
        return await rollback.run(seed)
    raise ValueError(f"{kind.value} is not a drill")


__all__ = ["DRILLS", "DRILL_VERSION", "Case", "DrillReport", "run_drill"]
