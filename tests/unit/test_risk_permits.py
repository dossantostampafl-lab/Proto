from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from services.execution import OrderType, TradeIntent, VenueId
from services.risk.permits import PermitLedger, PermitRejected, PermitSigner

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
KEY = b"k" * 32


def intent(**overrides) -> TradeIntent:
    values = {
        "intent_id": uuid4(),
        "instrument_id": "BINANCE_SPOT:BTCUSDT",
        "venue": VenueId.BINANCE_SPOT,
        "side": "BUY",
        "quantity": Decimal("0.001"),
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal("62000.10"),
        "strategy_id": "hybrid-fusion-v1",
        "model_version": "fusion-v1",
        "signal_ids": (uuid4(),),
        "created_at": NOW,
    }
    values.update(overrides)
    return TradeIntent.model_validate(values)


def rejected(code: str, call) -> None:
    with pytest.raises(PermitRejected) as error:
        call()
    assert error.value.code == code


def test_issued_permit_verifies_and_is_bound_to_the_intent() -> None:
    signer = PermitSigner(KEY)
    trade = intent()
    permit = signer.issue(trade, now=NOW)
    assert permit.expires_at - permit.issued_at == timedelta(seconds=10)
    assert permit.intent_fingerprint == trade.fingerprint()
    signer.verify(permit, trade, now=NOW + timedelta(seconds=9))


def test_forged_or_tampered_permits_fail_the_signature() -> None:
    signer = PermitSigner(KEY)
    trade = intent()
    permit = signer.issue(trade, now=NOW)
    rejected("bad_signature", lambda: PermitSigner(b"x" * 32).verify(permit, trade, now=NOW))
    stretched = permit.model_copy(update={"expires_at": NOW + timedelta(seconds=50)})
    rejected("bad_signature", lambda: signer.verify(stretched, trade, now=NOW))
    enlarged = permit.model_copy(update={"max_quantity": Decimal("1")})
    rejected("bad_signature", lambda: signer.verify(enlarged, trade, now=NOW))


def test_permit_does_not_cover_a_changed_intent() -> None:
    signer = PermitSigner(KEY)
    trade = intent()
    permit = signer.issue(trade, now=NOW)
    bigger = trade.model_copy(update={"quantity": Decimal("0.002")})
    other_side = trade.model_copy(update={"side": "SELL"})
    rejected("intent_mismatch", lambda: signer.verify(permit, bigger, now=NOW))
    rejected("intent_mismatch", lambda: signer.verify(permit, other_side, now=NOW))
    rejected("intent_mismatch", lambda: signer.verify(permit, intent(), now=NOW))


def test_permits_expire_and_are_not_valid_before_issue() -> None:
    signer = PermitSigner(KEY)
    trade = intent()
    permit = signer.issue(trade, now=NOW)
    rejected("expired", lambda: signer.verify(permit, trade, now=NOW + timedelta(seconds=10)))
    rejected("expired", lambda: signer.verify(permit, trade, now=NOW - timedelta(seconds=1)))


def test_ledger_enforces_single_use() -> None:
    signer = PermitSigner(KEY)
    ledger = PermitLedger(signer)
    trade = intent()
    permit = signer.issue(trade, now=NOW)
    ledger.consume(permit, trade, now=NOW)
    rejected("already_used", lambda: ledger.consume(permit, trade, now=NOW + timedelta(seconds=1)))
    second = signer.issue(trade, now=NOW)
    ledger.consume(second, trade, now=NOW)


def test_spent_permits_are_pruned_after_expiry() -> None:
    signer = PermitSigner(KEY)
    ledger = PermitLedger(signer)
    trade = intent()
    ledger.consume(signer.issue(trade, now=NOW), trade, now=NOW)
    ledger.consume(
        signer.issue(trade, now=NOW + timedelta(seconds=30)), trade, now=NOW + timedelta(seconds=30)
    )
    assert len(ledger._spent) == 1


def test_signer_rejects_weak_keys_and_long_ttls() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        PermitSigner(b"short")
    with pytest.raises(ValueError, match="60 seconds"):
        PermitSigner(KEY, ttl=timedelta(seconds=61))
