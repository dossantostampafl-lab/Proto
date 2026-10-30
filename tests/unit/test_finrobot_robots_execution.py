from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

pytest.importorskip("services.finrobots.contracts", reason="requires FinRobots PR #360")

from services.finrobots import CycleContext, FinRobotId, Stance  # noqa: E402
from services.finrobots.robots import default_robots  # noqa: E402


def supervisor():
    return next(r for r in default_robots() if r.robot_id == FinRobotId.EXECUTION_SUPERVISOR)


def context(status="OK", read_at=None, orders=None):
    now = datetime.now(UTC)
    return CycleContext(
        uuid4(),
        now,
        ("BINANCE_SPOT:BTCUSDT",),
        {
            "oms.orders": {"orders": orders if orders is not None else []},
            "_sources": {
                "sources": {
                    "oms.orders": {
                        "status": status,
                        "read_at": read_at or now.isoformat(),
                        "provenance": "PAPER",
                    }
                }
            },
        },
    )


@pytest.mark.asyncio
async def test_unknown_order_is_a_scoped_advisory_warning():
    ctx = context(orders=[{"status": "UNKNOWN", "instrument_id": "BINANCE_SPOT:BTCUSDT"}])
    reports = await supervisor().analyze(ctx)
    assert reports[0].stance is Stance.CAUTION
    assert reports[0].instrument_id == "BINANCE_SPOT:BTCUSDT"
    assert reports[0].evidence[0].source == "oms.orders"
    assert reports[0].real_money_execution is False


@pytest.mark.asyncio
async def test_unavailable_or_empty_orders_cannot_certify_execution_health():
    for ctx in (
        context(),
        context(status="TIMEOUT", orders=[{"status": "UNKNOWN"}]),
        context(read_at=(datetime.now(UTC) + timedelta(days=1)).isoformat()),
    ):
        assert (await supervisor().analyze(ctx)).stance is Stance.ABSTAIN
