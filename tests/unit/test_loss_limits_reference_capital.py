"""Daily loss and drawdown are measured against at most the reference capital (R$ 10,000).

Found while routing the XP demo account, which holds R$ 1,000,000: 1% of that balance is
R$ 10,000, a hundred times the loss the constitution allows. The change only tightens.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.risk import AccountState
from services.risk.engine import LockdownReason, account_breach
from services.watchdog import Checks, Observation

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def state(*, equity: str, start: str, peak: str) -> AccountState:
    return AccountState(
        equity=Decimal(equity),
        start_of_day_equity=Decimal(start),
        peak_equity=Decimal(peak),
        gross_exposure=Decimal("0"),
        positions={},
    )


def test_a_hundred_reais_lost_on_a_million_is_one_percent_of_the_reference_capital() -> None:
    breach, daily_loss, _ = account_breach(
        state(equity="999900", start="1000000", peak="1000000")
    )
    assert breach is LockdownReason.DAILY_LOSS and daily_loss == Decimal("0.01")


def test_a_smaller_loss_on_a_million_is_not_a_breach() -> None:
    breach, daily_loss, drawdown = account_breach(
        state(equity="999950", start="1000000", peak="1000000")
    )
    assert breach is None and daily_loss == Decimal("0.005") and drawdown == Decimal("0.005")


def test_drawdown_from_a_high_peak_is_measured_against_the_reference_capital() -> None:
    # Up R$ 20,000 on the day (no daily loss), then R$ 500 off the peak = 5% of R$ 10,000.
    breach, daily_loss, drawdown = account_breach(
        state(equity="1019500", start="1000000", peak="1020000")
    )
    assert breach is LockdownReason.DRAWDOWN
    assert drawdown == Decimal("0.05") and daily_loss < 0


@pytest.mark.parametrize(
    ("equity", "start", "peak", "expected_loss"),
    [
        ("9900", "10000", "10000", Decimal("0.01")),  # at the reference capital
        ("4950", "5000", "5000", Decimal("0.01")),  # under it: the balance is the base, as before
        ("9990", "10000", "10000", Decimal("0.001")),
    ],
)
def test_accounts_at_or_under_the_reference_capital_are_measured_as_before(
    equity, start, peak, expected_loss
) -> None:
    _, daily_loss, _ = account_breach(state(equity=equity, start=start, peak=peak))
    assert daily_loss == expected_loss


def test_an_unknown_baseline_still_counts_as_breached() -> None:
    breach, daily_loss, drawdown = account_breach(state(equity="100", start="0", peak="0"))
    assert breach is LockdownReason.DAILY_LOSS and daily_loss == drawdown == Decimal("1")


def obs(equity: str, start: str, peak: str) -> Observation:
    account = {
        "equity": equity,
        "start_of_day_equity": start,
        "peak_equity": peak,
        "gross_exposure": "0",
        "positions": {},
    }
    floor = {
        "journal": "VALID",
        "accounts": {"BRL": account},
        "controls": {"global_kill": False},
        "pipeline": {"paper_execution_enabled": True, "execution_backend": "paper"},
    }
    return Observation(at=NOW, api_health=True, floor=floor, market_ready=True)


def test_the_watchdog_applies_the_same_base() -> None:
    trips, _ = Checks().evaluate(obs(equity="999900", start="1000000", peak="1000000"))
    assert "daily_loss:BRL" in trips
    trips, _ = Checks().evaluate(obs(equity="999950", start="1000000", peak="1000000"))
    assert not any(t.startswith(("daily_loss", "drawdown")) for t in trips)
    trips, _ = Checks().evaluate(obs(equity="1019500", start="1000000", peak="1020000"))
    assert "drawdown:BRL" in trips
