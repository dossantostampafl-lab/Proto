from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from services.execution import OrderType, ReleaseStage, TradableInstrument, TradeIntent, VenueId
from services.execution.catalog import PAPER_INSTRUMENTS
from services.execution.constitution import CANARY_CAPITAL, canary_capital_in
from services.execution.venue import Quote
from services.market_data.instruments import AssetClass, SessionType
from services.risk import (
    AccountState,
    Controls,
    GovernorError,
    KillScope,
    LockdownReason,
    PermitSigner,
    RiskEngine,
    RiskGovernor,
    RiskPolicy,
    session_open,
)

NOW = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)  # Tuesday, 12:00 in Sao Paulo
BTC_ID = "BINANCE_SPOT:BTCUSDT"
QUOTE = Quote(bid=Decimal("62000.00"), ask=Decimal("62000.20"), observed_at=NOW)
PETR4 = TradableInstrument(
    instrument_id="XP_B3:PETR4",
    venue=VenueId.XP_B3,
    asset_class=AssetClass.EQUITY,
    base_asset="PETR4",
    quote_asset="BRL",
    tick_size=Decimal("0.01"),
    lot_size=Decimal("1"),
    min_quantity=Decimal("1"),
    min_notional=Decimal("1"),
    session_type=SessionType.EXCHANGE_SESSION,
    timezone="America/Sao_Paulo",
)
SIGNER = PermitSigner(b"k" * 32)


def engine(**policy) -> RiskEngine:
    return RiskEngine(
        instruments={**PAPER_INSTRUMENTS, PETR4.instrument_id: PETR4},
        signer=SIGNER,
        policy=RiskPolicy(**policy),
    )


def account(**overrides) -> AccountState:
    values = {
        "equity": Decimal("10000"),
        "start_of_day_equity": Decimal("10000"),
        "peak_equity": Decimal("10000"),
        "gross_exposure": Decimal("0"),
        "positions": {},
    }
    values.update(overrides)
    return AccountState(**values)


def intent(**overrides) -> TradeIntent:
    values = {
        "intent_id": uuid4(),
        "instrument_id": BTC_ID,
        "venue": VenueId.BINANCE_SPOT,
        "side": "BUY",
        "quantity": Decimal("0.002"),  # 124 notional -> 2.48 risk at 2% stop
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal("62000.20"),
        "strategy_id": "hybrid-fusion-v1",
        "model_version": "fusion-v1",
        "signal_ids": (uuid4(),),
        "created_at": NOW,
    }
    values.update(overrides)
    return TradeIntent.model_validate(values)


def evaluate(trade=None, *, acct=None, controls=None, quote=QUOTE, now=NOW, **policy):
    return engine(**policy).evaluate(
        trade or intent(),
        account=acct or account(),
        controls=controls or Controls(),
        quote=quote,
        now=now,
    )


def test_compliant_intent_is_approved_with_a_verifiable_permit() -> None:
    trade = intent()
    decision = evaluate(trade)
    assert decision.approved is True
    assert decision.rejections == ()
    SIGNER.verify(decision.permit, trade, now=NOW)
    assert all(v == "ok" for k, v in decision.checks.items() if k not in {"daily_loss", "drawdown"})
    assert decision.to_dict()["permit_id"] == str(decision.permit.permit_id)
    assert "signature" not in str(decision.to_dict())


@pytest.mark.parametrize(
    ("trade", "rejection"),
    [
        (intent(stage=ReleaseStage.CANARY), "stage_certified"),
        (intent(stage=ReleaseStage.LIVE), "stage_certified"),
        (intent(instrument_id="BINANCE_SPOT:DOGEUSDT"), "instrument_known"),
        (intent(quantity=Decimal("0.0000150")), "venue_grid"),
        (intent(limit_price=Decimal("62400.00")), "price_collar"),
        (intent(quantity=Decimal("0.3")), "risk_per_position"),
        (intent(side="SELL", reduce_only=True), "spot_no_short"),
        (intent(side="SELL"), "sell_is_reduce_only"),
    ],
)
def test_each_violation_rejects_without_a_permit(trade, rejection) -> None:
    decision = evaluate(trade)
    assert decision.approved is False
    assert decision.permit is None
    assert rejection in decision.rejections


def test_risk_per_position_matches_the_constitution() -> None:
    # budget = 10000 * 0.25% = 25; 25 / 2% = 1250 notional max
    at_limit = intent(quantity=Decimal("0.02016"))  # 1249.92 notional
    over = intent(quantity=Decimal("0.02017"))
    assert evaluate(at_limit).approved is True
    assert "risk_per_position" in evaluate(over).rejections
    # equity above reference capital does not raise the budget
    assert (
        "risk_per_position"
        in evaluate(
            over,
            acct=account(
                equity=Decimal("9999999"),
                start_of_day_equity=Decimal("9999999"),
                peak_equity=Decimal("9999999"),
            ),
        ).rejections
    )


def test_gross_leverage_counts_existing_exposure() -> None:
    decision = evaluate(acct=account(gross_exposure=Decimal("9900")))
    assert "gross_leverage" in decision.rejections


@pytest.mark.parametrize(
    "quote",
    [
        None,
        QUOTE.model_copy(update={"observed_at": NOW - timedelta(seconds=11)}),
        QUOTE.model_copy(update={"observed_at": NOW + timedelta(seconds=1)}),
        QUOTE.model_copy(update={"bid": Decimal("62001")}),
    ],
)
def test_bad_quotes_reject(quote) -> None:
    assert "fresh_quote" in evaluate(quote=quote).rejections


@pytest.mark.parametrize(
    ("controls", "rejection"),
    [
        (Controls(global_kill=True), "global_kill_switch"),
        (Controls(killed_venues=frozenset({"BINANCE_SPOT"})), "venue_kill_switch"),
        (Controls(killed_strategies=frozenset({"hybrid-fusion-v1"})), "strategy_kill_switch"),
        (Controls(lockdown=LockdownReason.DRAWDOWN), "no_lockdown"),
    ],
)
def test_kill_switches_and_lockdown_reject(controls, rejection) -> None:
    assert rejection in evaluate(controls=controls).rejections


def test_daily_loss_and_drawdown_breaches_are_reported_and_block_new_risk() -> None:
    loss = evaluate(acct=account(equity=Decimal("9900")))
    assert loss.breach is LockdownReason.DAILY_LOSS
    assert "no_lockdown" in loss.rejections
    dd = evaluate(
        acct=account(
            equity=Decimal("9500"),
            start_of_day_equity=Decimal("9500"),
            peak_equity=Decimal("10000"),
        )
    )
    assert dd.breach is LockdownReason.DRAWDOWN
    near = evaluate(acct=account(equity=Decimal("9901")))
    assert near.breach is None and near.approved


def test_risk_per_position_counts_what_is_already_held() -> None:
    # Budget 1250 notional (see above). 0.015 BTC held (~930) + 0.006 new (~372) > 1250:
    # stacked small buys must not build a position above the 0.25% budget.
    held = account(positions={BTC_ID: Decimal("0.015")}, gross_exposure=Decimal("930"))
    stacked = evaluate(intent(quantity=Decimal("0.006")), acct=held)
    assert "risk_per_position" in stacked.rejections
    assert stacked.permit is None
    # The same order on a flat book is fine, and a top-up within the budget still passes.
    assert evaluate(intent(quantity=Decimal("0.006"))).approved is True
    assert evaluate(intent(quantity=Decimal("0.005")), acct=held).approved is True


def test_risk_per_position_counts_buys_still_working_at_the_venue() -> None:
    # Nothing filled yet, but 0.015 BTC of buys rest (or are UNKNOWN): they count like a fill.
    working = account(open_buys={BTC_ID: Decimal("0.015")})
    assert (
        "risk_per_position" in evaluate(intent(quantity=Decimal("0.006")), acct=working).rejections
    )
    # Exits are never blocked by the budget, working buys or not.
    exit_ = intent(
        side="SELL", reduce_only=True, quantity=Decimal("0.002"), limit_price=Decimal("62000.00")
    )
    held = account(positions={BTC_ID: Decimal("0.02")}, open_buys={BTC_ID: Decimal("0.02")})
    assert evaluate(exit_, acct=held).approved is True


def test_an_unknown_book_adds_no_risk() -> None:
    # The API's outage shape (executor unreachable or a holding without a mark): exposure unknown.
    unknown = account(gross_exposure=Decimal("Infinity"), positions={})
    decision = evaluate(intent(quantity=Decimal("0.002")), acct=unknown)
    assert "gross_leverage" in decision.rejections and decision.permit is None


def test_reduce_only_exits_stay_allowed_in_lockdown_but_not_under_kill_switch() -> None:
    exit_ = intent(
        side="SELL", reduce_only=True, quantity=Decimal("0.002"), limit_price=Decimal("62000.00")
    )
    held = account(equity=Decimal("9000"), positions={BTC_ID: Decimal("0.005")})
    decision = evaluate(exit_, acct=held, controls=Controls(lockdown=LockdownReason.DAILY_LOSS))
    assert decision.approved is True
    killed = evaluate(exit_, acct=held, controls=Controls(global_kill=True))
    assert killed.approved is False


def test_venue_cap_can_only_tighten() -> None:
    capped = evaluate(max_order_notional={"BINANCE_SPOT": Decimal("100")})
    assert "venue_order_notional" in capped.rejections


def test_a_wide_spread_adds_no_risk_but_exits_stay_allowed() -> None:
    wide = Quote(bid=Decimal("60000.00"), ask=Decimal("62000.20"), observed_at=NOW)  # ~328 bps
    buy = evaluate(intent(), quote=wide)
    assert "quote_spread" in buy.rejections and buy.permit is None
    assert evaluate(intent(), quote=wide, max_spread_bps=Decimal("400")).approved is True
    exit_ = intent(
        side="SELL", reduce_only=True, quantity=Decimal("0.002"), limit_price=Decimal("60000.00")
    )
    held = account(positions={BTC_ID: Decimal("0.005")})
    assert evaluate(exit_, acct=held, quote=wide).approved is True
    with pytest.raises(ValueError):
        RiskPolicy(max_spread_bps=Decimal("0"))


def test_an_exchange_without_a_modelled_session_is_closed() -> None:
    # Before: any other zone silently got B3's hours (10:00-17:55 there, open at NOW).
    tokyo = PETR4.model_copy(update={"instrument_id": "XP_B3:TKY", "timezone": "Asia/Tokyo"})
    assert session_open(tokyo, NOW.replace(hour=3)) is False  # 12:00 in Tokyo, a Tuesday
    assert session_open(tokyo, NOW) is False


def test_exchange_sessions_follow_b3_hours() -> None:
    assert session_open(PETR4, NOW) is True
    assert session_open(PETR4, NOW.replace(hour=21)) is False  # 18:00 local
    assert session_open(PETR4, datetime(2026, 10, 17, 15, 0, tzinfo=UTC)) is False  # Saturday
    assert session_open(PAPER_INSTRUMENTS[BTC_ID], datetime(2026, 10, 17, 3, tzinfo=UTC))
    closed = intent(
        instrument_id="XP_B3:PETR4",
        venue=VenueId.XP_B3,
        quantity=Decimal("10"),
        limit_price=Decimal("38.50"),
    )
    quote = Quote(bid=Decimal("38.49"), ask=Decimal("38.50"), observed_at=NOW.replace(hour=21))
    assert "session_open" in evaluate(closed, quote=quote, now=NOW.replace(hour=21)).rejections


def test_policy_validates_its_bounds() -> None:
    with pytest.raises(ValueError):
        RiskPolicy(price_collar_bps=Decimal("0"))
    with pytest.raises(ValueError):
        RiskPolicy(stop_distance=Decimal("1"))


# --- governor -----------------------------------------------------------------


def test_breach_latches_a_lockdown_that_only_an_operator_can_release() -> None:
    governor = RiskGovernor(engine())
    governor.assess(intent(), account=account(equity=Decimal("9850")), quote=QUOTE, now=NOW)
    assert governor.snapshot()["lockdown"] == "DAILY_LOSS"

    recovered = governor.assess(intent(), account=account(), quote=QUOTE, now=NOW)
    assert "no_lockdown" in recovered.rejections  # latched, not auto-cleared

    with pytest.raises(GovernorError, match="DAILY_LOSS"):
        governor.release_lockdown(account(equity=Decimal("9850")))
    governor.release_lockdown(account())
    assert governor.assess(intent(), account=account(), quote=QUOTE, now=NOW).approved


def test_kill_switches_by_scope() -> None:
    governor = RiskGovernor(engine())
    governor.trigger_kill(KillScope.STRATEGY, "hybrid-fusion-v1")
    assert not governor.assess(intent(), account=account(), quote=QUOTE, now=NOW).approved
    governor.release_kill(KillScope.STRATEGY, "hybrid-fusion-v1")
    governor.trigger_kill(KillScope.VENUE, "BINANCE_SPOT")
    assert not governor.assess(intent(), account=account(), quote=QUOTE, now=NOW).approved
    governor.release_kill(KillScope.VENUE, "BINANCE_SPOT")
    assert governor.assess(intent(), account=account(), quote=QUOTE, now=NOW).approved
    external = governor.assess(
        intent(), account=account(), quote=QUOTE, now=NOW, external_global_kill=True
    )
    assert "global_kill_switch" in external.rejections
    with pytest.raises(GovernorError):
        governor.trigger_kill(KillScope.VENUE)


def test_canary_needs_a_certified_scope_and_sizes_on_the_canary_capital() -> None:
    small = intent(stage=ReleaseStage.CANARY, quantity=Decimal("0.001"))  # 62 notional, 1.24 risk
    uncertified = engine().evaluate(
        small,
        account=account(),
        controls=Controls(),
        quote=QUOTE,
        now=NOW,
        scope_stage=ReleaseStage.PAPER,
    )
    assert "stage_certified" in uncertified.rejections

    certified = engine().evaluate(
        small,
        account=account(),
        controls=Controls(),
        quote=QUOTE,
        now=NOW,
        scope_stage=ReleaseStage.CANARY,
        canary_capital=CANARY_CAPITAL,
    )
    assert certified.approved, certified.checks

    # 124 notional -> 2.48 risk: fine on R$10,000, too much on the R$500 canary (1.25).
    larger = intent(stage=ReleaseStage.CANARY)
    capped = engine().evaluate(
        larger,
        account=account(),
        controls=Controls(),
        quote=QUOTE,
        now=NOW,
        scope_stage=ReleaseStage.CANARY,
        canary_capital=CANARY_CAPITAL,
    )
    assert "risk_per_position" in capped.rejections

    # Without a BRL rate for the quote currency a CANARY intent is refused.
    unknown = engine().evaluate(
        small,
        account=account(),
        controls=Controls(),
        quote=QUOTE,
        now=NOW,
        scope_stage=ReleaseStage.CANARY,
    )
    assert "canary_capital_known" in unknown.rejections
    # R$500 is about 90.9 USDT at 5.50 BRL/USDT: the 62 USDT order is now far too large.
    in_usdt = engine().evaluate(
        small,
        account=account(),
        controls=Controls(),
        quote=QUOTE,
        now=NOW,
        scope_stage=ReleaseStage.CANARY,
        canary_capital=canary_capital_in(Decimal("5.50")),
    )
    assert "risk_per_position" in in_usdt.rejections
    assert canary_capital_in(Decimal("1")) == CANARY_CAPITAL
    with pytest.raises(ValueError):
        canary_capital_in(Decimal("0"))

    live = engine().evaluate(
        intent(stage=ReleaseStage.LIVE, quantity=Decimal("0.001")),
        account=account(),
        controls=Controls(),
        quote=QUOTE,
        now=NOW,
        scope_stage=ReleaseStage.LIVE,
    )
    assert "stage_certified" in live.rejections


def test_a_buy_sized_on_a_held_position_passes_the_per_position_check() -> None:
    """Sizer and Risk Engine agree on the 0.25% budget per position (2026-10-28 drill refusal).

    Production: agents held BTC, the sizer ignored it and sized the full budget, and the Risk
    Engine refused the drill's buy with ``risk_per_position: 5.41 > 4.67``.
    """

    from services.agents import build_candidate, fuse
    from services.execution import Signal, SignalDirection, SignalSource

    reference = Decimal("1868")  # R$10,000 in USDT
    held = Decimal("0.0006")  # ~37 USDT, what the BTC agents held
    signal = Signal(
        signal_id=uuid4(),
        source=SignalSource.PROTO_STRATEGY,
        strategy_id="e2e-drill",
        instrument_id=BTC_ID,
        direction=SignalDirection.LONG,
        strength=1.0,
        timeframe="drill",
        observed_at=NOW,
        received_at=NOW,
    )
    decision = fuse([signal], instrument_id=BTC_ID, now=NOW)
    state = account(
        reference_capital=reference,
        positions={BTC_ID: held},
        gross_exposure=held * QUOTE.ask,
    )

    def assess(position_quantity: Decimal):
        sized = build_candidate(
            decision,
            instrument=PAPER_INSTRUMENTS[BTC_ID],
            quote=QUOTE,
            equity=reference,
            position_quantity=position_quantity,
            now=NOW,
        )
        assert sized.intent is not None
        return engine().evaluate(
            sized.intent, account=state, controls=Controls(), quote=QUOTE, now=NOW
        )

    # Sized as if nothing were held (the old sizer): refused per position.
    assert "risk_per_position" in assess(Decimal("0")).rejections
    # Sized on what is held: approved.
    approved = assess(held)
    assert approved.approved, approved.rejections
