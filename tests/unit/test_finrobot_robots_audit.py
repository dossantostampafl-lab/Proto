from datetime import UTC, datetime
from uuid import uuid4

import pytest

pytest.importorskip("services.finrobots.contracts", reason="requires FinRobots PR #360")

from services.finrobots import CycleContext, FinRobotId, Stance  # noqa: E402
from services.finrobots.robots import default_robots  # noqa: E402


def context(value):
    return CycleContext(uuid4(), datetime.now(UTC), (), {"agents.status": value})


def auditor():
    return next(robot for robot in default_robots() if robot.robot_id == FinRobotId.AUDIT_RECOVERY)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, {}, {"journal_failures": -1},
                                  {"journal_failures": True}, {"journal_failures": "1"},
                                  {"journal_failures": 0}])
async def test_auditor_abstains_without_positive_failure_count(value):
    assert (await auditor().analyze(context(value))).stance is Stance.ABSTAIN


@pytest.mark.asyncio
async def test_auditor_reports_process_local_journal_failure():
    ctx = context({"journal_failures": 2, "stage": "PAPER"})
    report = await auditor().analyze(ctx)
    assert report.stance is Stance.CAUTION
    assert report.cycle_id == ctx.cycle_id
    assert report.evidence[0].metrics["journal_failures"] == "2"
    assert report.real_money_execution is False
