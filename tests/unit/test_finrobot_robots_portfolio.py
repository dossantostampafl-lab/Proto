from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

pytest.importorskip("services.finrobots.contracts", reason="requires FinRobots PR #360")

from services.finrobots import CycleContext, Stance  # noqa: E402
from services.finrobots.robots import PortfolioIntelligence  # noqa: E402


def portfolio():
    return PortfolioIntelligence()


def context(account, *, source_status="OK", read_at=None):
    now = datetime.now(UTC)
    return CycleContext(
        uuid4(),
        now,
        (),
        {
            "oms.ledger": {"mode": "PAPER", "accounts": {"USDT": account}},
            "_sources": {
                "sources": {
                    "oms.ledger": {
                        "status": source_status,
                        "read_at": read_at or now.isoformat(),
                        "provenance": "PAPER",
                    }
                }
            },
        },
    )


@pytest.mark.asyncio
async def test_portfolio_reports_observed_account_values_without_approval():
    result = await portfolio().analyze(context({"equity": "10000.01", "gross_exposure": "25.25"}))
    assert result.stance is Stance.ABSTAIN
    assert result.evidence[0].metrics["equity"] == "10000.01"
    assert result.evidence[0].metrics["gross_exposure"] == "25.25"
    assert not result.real_money_execution


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "account",
    [
        None,
        {},
        {"equity": "NaN", "gross_exposure": "0"},
        {"equity": "1", "gross_exposure": "-Infinity"},
    ],
)
async def test_portfolio_abstains_without_finite_evidence(account):
    result = await portfolio().analyze(context(account))
    assert result.stance is Stance.ABSTAIN
    assert not result.evidence


@pytest.mark.asyncio
async def test_portfolio_rejects_old_and_unavailable_sources():
    account = {"equity": "100", "gross_exposure": "0"}
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    for ctx in (context(account, source_status="TIMEOUT"), context(account, read_at=old)):
        assert not (await portfolio().analyze(ctx)).evidence


@pytest.mark.asyncio
async def test_unmarked_exposure_is_reported_as_unknown_not_a_number():
    result = await portfolio().analyze(context({"equity": "100", "gross_exposure": "Infinity"}))
    assert result.stance is Stance.CAUTION
    assert "unknown" in result.headline.lower()
    assert "gross_exposure" not in result.evidence[0].metrics
