from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

pytest.importorskip("services.finrobots.contracts", reason="requires FinRobots PR #360")

from services.finrobots import CycleContext, FinRobotId, Stance  # noqa: E402
from services.finrobots.robots import default_robots  # noqa: E402


def context(balances, *, status="OK", read_at=None, unavailable=False):
    now = datetime.now(UTC)
    return CycleContext(
        uuid4(),
        now,
        (),
        {
            "oms.balances": {
                "accounts": {
                    "BINANCE_SPOT": {
                        "mode": "PAPER",
                        "balances": balances,
                        "unavailable": unavailable,
                    }
                }
            },
            "_sources": {
                "sources": {
                    "oms.balances": {
                        "status": status,
                        "read_at": read_at or now.isoformat(),
                        "provenance": "PAPER",
                    }
                }
            },
        },
    )


def portfolio():
    return next(r for r in default_robots() if r.robot_id == FinRobotId.PORTFOLIO_INTELLIGENCE)


@pytest.mark.asyncio
async def test_portfolio_reports_exact_balances_without_inferring_exposure():
    ctx = context({"USDT": {"total": "10000.01", "reserved": "25.25", "available": "9974.76"}})
    result = await portfolio().analyze(ctx)
    assert result.stance is Stance.ABSTAIN
    assert result.evidence[0].metrics == {
        "asset": "USDT",
        "total": "10000.01",
        "reserved": "25.25",
        "available": "9974.76",
    }
    assert "exposure" not in result.evidence[0].metrics
    assert result.real_money_execution is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "balance",
    [
        None,
        {},
        {"total": "NaN", "reserved": "0", "available": "0"},
        {"total": "1", "reserved": "Infinity", "available": "0"},
    ],
)
async def test_portfolio_rejects_incomplete_or_nonfinite_balances(balance):
    result = await portfolio().analyze(context({"USDT": balance}))
    assert result.stance is Stance.ABSTAIN
    assert not result.evidence


@pytest.mark.asyncio
async def test_portfolio_abstains_for_old_unavailable_or_missing_sources():
    balance = {"USDT": {"total": "1", "reserved": "0", "available": "1"}}
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    for ctx in (
        context(balance, status="TIMEOUT"),
        context(balance, read_at=old),
        context(balance, unavailable=True),
        context({}),
    ):
        assert not (await portfolio().analyze(ctx)).evidence
