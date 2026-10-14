from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app import main, risk_surface
from apps.api.app.app_state import runtime
from apps.api.app.event_state import operational_journal
from apps.api.app.models import KillSwitchState
from apps.api.app.settings import Settings
from services.execution import OrderType, TradeIntent, VenueId
from services.execution.venue import Quote
from services.risk import AccountState

TOKEN = "o" * 32
HEADERS = {"X-Proto-Operator-Token": TOKEN}


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    settings = Settings(operator_api_token=TOKEN, risk_permit_signing_key="s" * 32)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(risk_surface, "settings", settings)
    risk_surface.reset_risk_state()
    yield settings
    risk_surface.reset_risk_state()


def intent() -> TradeIntent:
    return TradeIntent(
        intent_id=uuid4(),
        instrument_id="BINANCE_SPOT:BTCUSDT",
        venue=VenueId.BINANCE_SPOT,
        side="BUY",
        quantity=Decimal("0.002"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("62000.20"),
        strategy_id="hybrid-fusion-v1",
        model_version="fusion-v1",
        signal_ids=(uuid4(),),
        created_at=datetime.now(UTC),
    )


def fresh_quote(*_args, **_kwargs) -> Quote:
    return Quote(bid=Decimal("62000.00"), ask=Decimal("62000.20"), observed_at=datetime.now(UTC))


async def test_assess_issues_a_consumable_permit_and_journals_without_signature(
    monkeypatch,
) -> None:
    monkeypatch.setattr(risk_surface, "live_quote", fresh_quote)
    trade = intent()
    decision = await risk_surface.assess(trade)
    assert decision.approved is True
    event = operational_journal.journal.recent(1)[0]
    assert event.event_type == "RISK_DECISION"
    assert decision.permit.signature not in event.payload_json
    risk_surface.permit_ledger().consume(decision.permit, trade, now=datetime.now(UTC))


async def test_no_quote_means_no_permit() -> None:
    decision = await risk_surface.assess(intent())
    assert decision.approved is False
    assert "fresh_quote" in decision.rejections


async def test_runtime_kill_switch_halts_risk_approvals(monkeypatch) -> None:
    monkeypatch.setattr(risk_surface, "live_quote", fresh_quote)
    monkeypatch.setattr(runtime, "kill_switch", KillSwitchState.TRIGGERED)
    decision = await risk_surface.assess(intent())
    assert "global_kill_switch" in decision.rejections


def test_kill_switch_routes_require_the_operator_and_are_journaled() -> None:
    client = TestClient(main.app)
    body = {"scope": "STRATEGY", "key": "hybrid-fusion-v1", "action": "TRIGGER"}
    assert client.post("/v1/risk/killswitch", json=body).status_code == 401
    response = client.post("/v1/risk/killswitch", json=body, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["controls"]["killed_strategies"] == ["hybrid-fusion-v1"]
    assert operational_journal.journal.recent(1)[0].event_type == "RISK_KILL_SWITCH_CHANGED"
    missing_key = {"scope": "VENUE", "action": "TRIGGER"}
    assert client.post("/v1/risk/killswitch", json=missing_key, headers=HEADERS).status_code == 422
    release = {**body, "action": "RELEASE"}
    released = client.post("/v1/risk/killswitch", json=release, headers=HEADERS).json()
    assert released["controls"]["killed_strategies"] == []


async def test_lockdown_release_is_refused_while_breached(monkeypatch) -> None:
    monkeypatch.setattr(risk_surface, "live_quote", fresh_quote)
    losing = AccountState(
        equity=Decimal("9800"),
        start_of_day_equity=Decimal("10000"),
        peak_equity=Decimal("10000"),
        gross_exposure=Decimal("0"),
        positions={},
    )
    async def losing_account(_quote_asset):
        return losing

    monkeypatch.setattr(risk_surface, "account_state", losing_account)
    decision = await risk_surface.assess(intent())
    assert decision.breach is not None
    assert operational_journal.journal.recent(1)[0].event_type == "RISK_LOCKDOWN_LATCHED"

    client = TestClient(main.app)
    refused = client.post("/v1/risk/lockdown/release", headers=HEADERS)
    assert refused.status_code == 409
    governor_before = risk_surface.governor()
    healthy = AccountState(
        equity=Decimal("10000"),
        start_of_day_equity=Decimal("10000"),
        peak_equity=Decimal("10000"),
        gross_exposure=Decimal("0"),
        positions={},
    )
    async def healthy_account(_quote_asset):
        return healthy

    monkeypatch.setattr(risk_surface, "account_state", healthy_account)
    assert risk_surface.governor().snapshot()["lockdown"] == "DAILY_LOSS"  # still latched
    released = client.post("/v1/risk/lockdown/release", headers=HEADERS)
    assert risk_surface.governor() is governor_before
    assert released.status_code == 200
    assert released.json()["controls"]["lockdown"] is None


def test_status_reports_constitution_and_never_real_money() -> None:
    status = TestClient(main.app).get("/v1/risk/status").json()
    assert status["constitution"]["max_risk_per_position"] == "0.0025"
    assert status["constitution"]["max_gross_leverage"] == "1.0"
    assert status["policy"]["signing_key"] == "configured"
    assert status["real_money_execution"] is False


def test_production_requires_a_strong_signing_key_when_set() -> None:
    production = {"app_env": "production", "operator_api_token": TOKEN}
    with pytest.raises(ValueError, match="RISK_PERMIT_SIGNING_KEY"):
        Settings(**production, risk_permit_signing_key="short")
    Settings(**production, risk_permit_signing_key="s" * 32)
