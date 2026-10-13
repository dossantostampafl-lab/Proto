from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from services.execution import (
    IllegalOrderTransition,
    LedgerEntry,
    LedgerEntryKind,
    OrderStatus,
    OrderType,
    RiskPermit,
    Signal,
    SignalDirection,
    SignalSource,
    TradableInstrument,
    TradeIntent,
    VenueId,
    transition,
)
from services.execution.contracts import TERMINAL_ORDER_STATUSES
from services.market_data.instruments import AssetClass, SessionType

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def btc_brl() -> TradableInstrument:
    return TradableInstrument(
        instrument_id="BINANCE_SPOT:BTCBRL",
        venue=VenueId.BINANCE_SPOT,
        asset_class=AssetClass.CRYPTO,
        base_asset="BTC",
        quote_asset="BRL",
        tick_size=Decimal("1"),
        lot_size=Decimal("0.00001"),
        min_quantity=Decimal("0.00001"),
        min_notional=Decimal("10"),
        session_type=SessionType.CONTINUOUS_24_7,
        timezone="UTC",
    )


def petr4() -> TradableInstrument:
    return TradableInstrument(
        instrument_id="XP_B3:PETR4",
        venue=VenueId.XP_B3,
        asset_class=AssetClass.EQUITY,
        base_asset="PETR4",
        quote_asset="BRL",
        tick_size=Decimal("0.01"),
        lot_size=Decimal("1"),
        min_quantity=Decimal("1"),
        min_notional=Decimal("0.01"),
        session_type=SessionType.EXCHANGE_SESSION,
        timezone="America/Sao_Paulo",
    )


def intent(**overrides: object) -> TradeIntent:
    values: dict[str, object] = {
        "intent_id": uuid4(),
        "instrument_id": "BINANCE_SPOT:BTCBRL",
        "venue": VenueId.BINANCE_SPOT,
        "side": "BUY",
        "quantity": Decimal("0.001"),
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal("350000"),
        "strategy_id": "imbalance-v1",
        "model_version": "2026.10.13",
        "signal_ids": (uuid4(),),
        "created_at": NOW,
    }
    values.update(overrides)
    return TradeIntent.model_validate(values)


# --- instruments -----------------------------------------------------------


def test_instrument_id_must_be_namespaced_by_its_venue() -> None:
    with pytest.raises(ValidationError, match="namespaced"):
        TradableInstrument.model_validate(
            {**btc_brl().model_dump(), "instrument_id": "XP_B3:BTCBRL"}
        )


def test_prediction_markets_are_not_tradable_instruments() -> None:
    with pytest.raises(ValidationError, match="prediction"):
        TradableInstrument.model_validate(
            {**btc_brl().model_dump(), "asset_class": AssetClass.PREDICTION_MARKET}
        )


def test_instrument_rejects_invalid_timezone() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        TradableInstrument.model_validate({**petr4().model_dump(), "timezone": "Mars/Base"})


def test_instrument_grid_checks_price_quantity_and_notional() -> None:
    instrument = btc_brl()
    assert instrument.order_violation(Decimal("0.001"), Decimal("350000")) is None
    assert instrument.order_violation(Decimal("0.000015"), Decimal("350000")) == (
        "quantity not on lot grid"
    )
    assert instrument.order_violation(Decimal("0.001"), Decimal("350000.5")) == (
        "price not on tick grid"
    )
    assert instrument.order_violation(Decimal("0.00001"), Decimal("350000")) == (
        "notional below venue minimum"
    )
    assert petr4().order_violation(Decimal("0"), Decimal("38.5")) == "quantity below venue minimum"


# --- signals ----------------------------------------------------------------


def test_signal_strength_is_bounded_and_flat_signals_are_allowed() -> None:
    signal = Signal(
        signal_id=uuid4(),
        source=SignalSource.TRADINGVIEW,
        strategy_id="tv-ema-cross",
        instrument_id="BINANCE_SPOT:BTCBRL",
        direction=SignalDirection.FLAT,
        strength=0.0,
        timeframe="15m",
        observed_at=NOW,
        received_at=NOW + timedelta(seconds=1),
    )
    assert signal.direction is SignalDirection.FLAT
    with pytest.raises(ValidationError):
        Signal.model_validate({**signal.model_dump(), "strength": 1.5})


def test_signal_cannot_be_received_before_it_was_observed() -> None:
    with pytest.raises(ValidationError, match="received_at"):
        Signal(
            signal_id=uuid4(),
            source=SignalSource.PROTO_STRATEGY,
            strategy_id="imbalance-v1",
            instrument_id="BINANCE_SPOT:BTCBRL",
            direction=SignalDirection.LONG,
            strength=0.7,
            timeframe="tick",
            observed_at=NOW,
            received_at=NOW - timedelta(seconds=1),
        )


# --- intents ------------------------------------------------------------------


def test_limit_intent_requires_price_and_market_intent_forbids_it() -> None:
    with pytest.raises(ValidationError, match="limit_price"):
        intent(limit_price=None)
    with pytest.raises(ValidationError, match="limit_price"):
        intent(order_type=OrderType.MARKET)
    assert intent(order_type=OrderType.MARKET, limit_price=None).limit_price is None


def test_intent_must_reference_at_least_one_signal_and_match_its_venue() -> None:
    with pytest.raises(ValidationError):
        intent(signal_ids=())
    with pytest.raises(ValidationError, match="namespaced"):
        intent(venue=VenueId.XP_B3)


def test_intent_rejects_non_finite_and_non_positive_quantities() -> None:
    for quantity in (Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValidationError):
            intent(quantity=quantity)


def test_intent_fingerprint_is_stable_and_covers_every_field() -> None:
    first = intent()
    assert first.fingerprint() == TradeIntent.model_validate(first.model_dump()).fingerprint()
    assert len(first.fingerprint()) == 64
    assert (
        first.fingerprint() != first.model_copy(update={"quantity": Decimal("0.002")}).fingerprint()
    )
    assert first.fingerprint() != first.model_copy(update={"reduce_only": True}).fingerprint()


def test_intent_is_immutable() -> None:
    with pytest.raises(ValidationError):
        intent().quantity = Decimal("1")  # type: ignore[misc]


def test_client_order_id_is_deterministic_per_intent_and_attempt() -> None:
    first = intent()
    assert first.client_order_id(0) == first.client_order_id(0)
    assert first.client_order_id(0) != first.client_order_id(1)
    assert len(first.client_order_id(0)) <= 32
    assert first.client_order_id(0).isalnum()


# --- permits ------------------------------------------------------------------


def test_permit_lifetime_is_short_and_ordered() -> None:
    trade = intent()
    permit = RiskPermit(
        permit_id=uuid4(),
        intent_id=trade.intent_id,
        intent_fingerprint=trade.fingerprint(),
        max_quantity=trade.quantity,
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=10),
        signature="0" * 64,
    )
    assert permit.covers(trade, now=NOW + timedelta(seconds=5))
    assert not permit.covers(trade, now=NOW + timedelta(seconds=10))
    assert not permit.covers(trade.model_copy(update={"quantity": Decimal("1")}), now=NOW)
    with pytest.raises(ValidationError, match="lifetime"):
        RiskPermit.model_validate(
            {**permit.model_dump(), "expires_at": NOW + timedelta(seconds=61)}
        )
    with pytest.raises(ValidationError, match="lifetime"):
        RiskPermit.model_validate({**permit.model_dump(), "expires_at": NOW})


def test_permit_does_not_cover_a_different_intent_with_the_same_id() -> None:
    trade = intent()
    permit = RiskPermit(
        permit_id=uuid4(),
        intent_id=trade.intent_id,
        intent_fingerprint=trade.fingerprint(),
        max_quantity=Decimal("10"),
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=10),
        signature="0" * 64,
    )
    tampered = trade.model_copy(update={"side": "SELL"})
    assert not permit.covers(tampered, now=NOW)


# --- order state machine --------------------------------------------------------


def test_happy_path_order_transitions() -> None:
    status = OrderStatus.NEW
    for nxt in (
        OrderStatus.SUBMITTED,
        OrderStatus.ACKNOWLEDGED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
    ):
        status = transition(status, nxt)
    assert status is OrderStatus.FILLED


def test_terminal_states_never_move() -> None:
    for terminal in TERMINAL_ORDER_STATUSES:
        for nxt in OrderStatus:
            with pytest.raises(IllegalOrderTransition):
                transition(terminal, nxt)


def test_unknown_can_only_resolve_to_a_venue_confirmed_state() -> None:
    assert transition(OrderStatus.SUBMITTED, OrderStatus.UNKNOWN) is OrderStatus.UNKNOWN
    for resolved in (
        OrderStatus.ACKNOWLEDGED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
    ):
        assert transition(OrderStatus.UNKNOWN, resolved) is resolved
    with pytest.raises(IllegalOrderTransition):
        transition(OrderStatus.UNKNOWN, OrderStatus.SUBMITTED)
    with pytest.raises(IllegalOrderTransition):
        transition(OrderStatus.NEW, OrderStatus.FILLED)


# --- ledger -------------------------------------------------------------------


def test_ledger_entry_signs_follow_kind() -> None:
    LedgerEntry(
        entry_id=uuid4(),
        account_id="paper-1",
        venue=VenueId.PAPER,
        asset="BRL",
        amount=Decimal("-3.5"),
        kind=LedgerEntryKind.FEE,
        reference_id="PX123",
        recorded_at=NOW,
    )
    with pytest.raises(ValidationError, match="fee"):
        LedgerEntry(
            entry_id=uuid4(),
            account_id="paper-1",
            venue=VenueId.PAPER,
            asset="BRL",
            amount=Decimal("3.5"),
            kind=LedgerEntryKind.FEE,
            reference_id="PX123",
            recorded_at=NOW,
        )
    with pytest.raises(ValidationError):
        LedgerEntry(
            entry_id=uuid4(),
            account_id="paper-1",
            venue=VenueId.PAPER,
            asset="BRL",
            amount=Decimal("0"),
            kind=LedgerEntryKind.TRADE,
            reference_id="PX123",
            recorded_at=NOW,
        )


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        intent(created_at=datetime(2026, 10, 13, 12, 0))
