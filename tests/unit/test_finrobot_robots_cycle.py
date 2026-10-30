import pytest

pytest.importorskip("services.finrobots.contracts", reason="requires FinRobots PR #360")

from services.finrobots import FinRobotOrchestrator, FinRobotRegistry  # noqa: E402
from services.finrobots.robots import default_robots  # noqa: E402


@pytest.mark.asyncio
async def test_real_specialists_run_through_council_without_chatgpt():
    async def snapshot():
        return {
            "risk.status": {"controls": {"global_kill": True}},
            "agents.status": {"journal_failures": 1, "stage": "PAPER"},
        }

    registry = FinRobotRegistry()
    for robot in default_robots():
        registry.bind(robot)
    brain = FinRobotOrchestrator(registry, snapshot=snapshot, instruments=lambda: ("BTC",))
    cycle = await brain.run_cycle()
    assert not cycle.errors
    assert len(cycle.reports) == 4
    assert any(scope.verdict.value == "BLOCK_NEW_RISK" for scope in cycle.council.scopes)
    assert all(not report.real_money_execution for report in cycle.reports)
