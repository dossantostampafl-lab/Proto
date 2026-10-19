"""R$ 10,000 held in the account's own quote currency, and one valuation per held asset."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from apps.api.app import agent_surface, fx_rates, main, oms_surface
from services.execution import LedgerEntry, LedgerEntryKind, VenueId
from services.execution.constitution import (
    REFERENCE_CAPITAL,
    conservative_equity,
    reference_capital_in,
)
from services.oms.ledger import entry_id
from services.risk import AccountState, LockdownReason, account_breach
from services.watchdog.checks import Checks, Observation
from tests.conftest import REAL_REFERENCE_CAPITAL
from tests.unit.test_oms_surface import _reset, configure, live_snapshot  # noqa: F401


def _account(equity: str, sod: str, peak: str, reference: Decimal) -> AccountState:
    return AccountState(
        equity=Decimal(equity),
        start_of_day_equity=Decimal(sod),
        peak_equity=Decimal(peak),
        gross_exposure=Decimal("0"),
        positions={},
        reference_capital=reference,
    )


def test_reference_capital_converts_at_the_rate_and_refuses_a_bad_one() -> None:
    assert reference_capital_in(Decimal("5.08")).quantize(Decimal("0.01")) == Decimal("1968.50")
    assert reference_capital_in(Decimal("1")) == REFERENCE_CAPITAL
    with pytest.raises(ValueError):
        reference_capital_in(Decimal("0"))


def test_conservative_equity_is_capped_by_the_account_reference() -> None:
    usdt = reference_capital_in(Decimal("5"))  # 2000 USDT
    assert conservative_equity(Decimal("10160"), usdt) == Decimal("2000")
    assert conservative_equity(Decimal("1500"), usdt) == Decimal("1500")
    assert conservative_equity(Decimal("10160"), Decimal("0")) == 0  # unknown sizes nothing
    assert conservative_equity(Decimal("10160")) == REFERENCE_CAPITAL  # BRL unchanged


def test_usdt_loss_is_measured_against_r10000_worth_of_usdt() -> None:
    usdt = reference_capital_in(Decimal("5"))  # 2000 USDT: 1% = 20, 5% = 100
    assert account_breach(_account("10140", "10150", "10150", usdt))[0] is None
    daily = account_breach(_account("10130", "10150", "10150", usdt))[0]
    assert daily is LockdownReason.DAILY_LOSS
    drawdown = account_breach(_account("10050", "10050", "10160", usdt))[0]
    assert drawdown is LockdownReason.DRAWDOWN
    # The same drawdown against 10,000 taken as a plain number stays under 5%.
    assert account_breach(_account("10050", "10050", "10160", REFERENCE_CAPITAL))[0] is None


def test_unknown_reference_keeps_the_plain_basis_never_a_looser_one() -> None:
    unknown = _account("10050", "10050", "10160", Decimal("0"))
    plain = _account("10050", "10050", "10160", REFERENCE_CAPITAL)
    assert account_breach(unknown) == account_breach(plain)


def _observe(account: dict[str, str]) -> list[str]:
    obs = Observation(
        at=datetime.now(UTC),
        api_health=True,
        floor={"journal": "VALID", "accounts": {"USDT": account}},
        market_ready=True,
    )
    return Checks().evaluate(obs)[0]


def test_watchdog_uses_the_reported_reference_capital() -> None:
    account = {"equity": "10050", "start_of_day_equity": "10050", "peak_equity": "10160"}
    assert "drawdown:USDT" not in _observe(account)
    assert "drawdown:USDT" in _observe({**account, "reference_capital": "2000"})
    # A reported reference is never allowed to loosen the plain number.
    assert "drawdown:USDT" not in _observe({**account, "reference_capital": "50000"})


class _Rates:
    def __init__(self, rate: Decimal | None) -> None:
        self.rate = rate

    async def brl_per(self, quote_asset: str) -> Decimal | None:
        return Decimal("1") if quote_asset == "BRL" else self.rate


async def test_account_state_reports_the_reference_in_its_quote_currency(monkeypatch) -> None:
    configure(monkeypatch)
    monkeypatch.setattr(oms_surface, "_reference_capital", REAL_REFERENCE_CAPITAL)
    monkeypatch.setattr(fx_rates, "_rates", _Rates(Decimal("5")))
    await oms_surface.components()
    assert (await oms_surface.account_state("USDT")).reference_capital == Decimal("2000")
    assert (await oms_surface.account_state("BRL")).reference_capital == REFERENCE_CAPITAL

    monkeypatch.setattr(fx_rates, "_rates", _Rates(None))
    assert (await oms_surface.account_state("USDT")).reference_capital == 0


async def test_btc_bought_on_usdt_does_not_make_the_brl_account_unknown(monkeypatch) -> None:
    configure(monkeypatch)
    _, ledger, _ = await oms_surface.components()
    await ledger.post(
        [
            LedgerEntry(
                entry_id=entry_id("test", "btc"),
                account_id=oms_surface.ACCOUNT_ID,
                venue=VenueId.BINANCE_SPOT,
                asset="BTC",
                amount=Decimal("0.01"),
                kind=LedgerEntryKind.TRADE,
                reference_id="test",
                recorded_at=datetime.now(UTC),
            )
        ]
    )

    def usdt_only(symbol: str):
        return None if symbol.endswith("BRL") else live_snapshot(symbol)

    monkeypatch.setattr(agent_surface.live_monitor, "snapshot", usdt_only)
    assert (await oms_surface.account_state("BRL")).gross_exposure == 0
    assert (await oms_surface.account_state("USDT")).gross_exposure == Decimal("620.0010")

    # Marked nowhere: exposure really is unknown, and the account still blocks new risk.
    monkeypatch.setattr(agent_surface.live_monitor, "snapshot", lambda _s: None)
    assert (await oms_surface.account_state("BRL")).gross_exposure == Decimal("Infinity")


def test_health_labels_are_truthful(monkeypatch) -> None:
    monkeypatch.setenv("PROTO_GIT_COMMIT_SHA", "abc1234")
    with TestClient(main.app) as client:
        body = client.get("/health").json()
    assert body["system_mode"] == main.settings.system_mode
    assert body["account_source"] in {"PAPER", "TESTNET"}
    assert body["git_commit_sha"] == "abc1234"
    assert set(body["control_plane"]) == {"status", "durable"}
    assert body["financial_connectivity"] is False
    assert body["real_money_execution"] is False
