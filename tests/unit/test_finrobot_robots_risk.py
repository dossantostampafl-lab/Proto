from datetime import UTC, datetime
from uuid import uuid4

import pytest

pytest.importorskip("services.finrobots.contracts", reason="requires FinRobots PR #360")

from services.finrobots import CycleContext, FinRobotId, Stance  # noqa: E402
from services.finrobots.robots import default_robots  # noqa: E402


def context(controls):
    snapshot = {} if controls is None else {"risk.status": {"controls": controls}}
    return CycleContext(uuid4(), datetime.now(UTC), ("BINANCE_SPOT:BTCUSDT",), snapshot)


def guardian():
    return next(robot for robot in default_robots() if robot.robot_id == FinRobotId.RISK_GUARDIAN)


@pytest.mark.asyncio
async def test_risk_guardian_abstains_without_complete_evidence():
    for controls in (None, {}, {"global_kill": False}):
        result = await guardian().analyze(context(controls))
        assert result.stance is Stance.ABSTAIN
        assert not result.evidence


@pytest.mark.asyncio
async def test_risk_guardian_vetoes_global_kill_without_order_authority():
    ctx = context({"global_kill": True, "runtime_kill_switch": "ARMED", "lockdown": None})
    result = await guardian().analyze(ctx)
    assert result.stance is Stance.VETO
    assert result.cycle_id == ctx.cycle_id
    assert result.evidence[0].source == "risk.status"
    assert result.financial_connectivity is False
    assert result.real_money_execution is False


@pytest.mark.asyncio
async def test_risk_guardian_does_not_approve_risk_when_controls_are_clear():
    ctx = context({"global_kill": False, "runtime_kill_switch": "ARMED", "lockdown": None})
    result = await guardian().analyze(ctx)
    assert result.stance is Stance.ABSTAIN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "controls",
    [
        {"global_kill": False, "runtime_kill_switch": "TRIGGERED", "lockdown": None},
        {"global_kill": False, "runtime_kill_switch": "ARMED", "lockdown": "DRAWDOWN"},
    ],
)
async def test_risk_guardian_detects_other_global_controls(controls):
    assert (await guardian().analyze(context(controls))).stance is Stance.VETO
