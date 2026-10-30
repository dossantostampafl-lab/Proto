from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

pytest.importorskip("services.finrobots.contracts", reason="requires FinRobots PR #360")

from services.finrobots import CycleContext, FinRobotId, Stance  # noqa: E402
from services.finrobots.robots import default_robots  # noqa: E402


def market():
    return next(r for r in default_robots() if r.robot_id == FinRobotId.MARKET_INTELLIGENCE)


@pytest.mark.asyncio
async def test_market_abstains_without_timestamped_quotes_or_configured_freshness():
    ctx = CycleContext(uuid4(), datetime.now(UTC), ("VENUE:BTC",), {})
    report = await market().analyze(ctx)
    assert report.stance is Stance.ABSTAIN


@pytest.mark.asyncio
async def test_market_flags_stale_quote_using_existing_risk_threshold():
    now = datetime.now(UTC)
    ctx = CycleContext(
        uuid4(),
        now,
        ("VENUE:BTC",),
        {
            "market.quotes": {
                "VENUE:BTC": {"observed_at": (now - timedelta(seconds=30)).isoformat()}
            },
            "risk.status": {"policy": {"max_quote_age_seconds": 10}},
            "_sources": {
                "version": "finrobots-snapshot/1.1",
                "sources": {
                    "market.quotes": {"status": "OK", "provenance": "LIVE"},
                    "risk.status": {"status": "OK"},
                },
            },
        },
    )
    reports = await market().analyze(ctx)
    assert reports[0].instrument_id == "VENUE:BTC"
    assert reports[0].stance is Stance.CAUTION
    assert reports[0].evidence[0].provenance.value == "STALE"


@pytest.mark.asyncio
@pytest.mark.parametrize("max_age", [None, 0, -1, float("nan"), True])
async def test_market_does_not_invent_a_freshness_threshold(max_age):
    now = datetime.now(UTC)
    ctx = CycleContext(
        uuid4(),
        now,
        ("VENUE:BTC",),
        {
            "market.quotes": {"VENUE:BTC": {"observed_at": (now - timedelta(days=1)).isoformat()}},
            "risk.status": {"policy": {"max_quote_age_seconds": max_age}},
            "_sources": {
                "sources": {
                    "market.quotes": {"status": "OK", "provenance": "LIVE"},
                    "risk.status": {"status": "OK"},
                }
            },
        },
    )
    assert (await market().analyze(ctx)).stance is Stance.ABSTAIN
