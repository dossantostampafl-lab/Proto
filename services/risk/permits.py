"""Signed, short-lived, single-use Risk Permits.

Only the Risk Engine holds the signing key. A permit is an HMAC-SHA256 over its
own fields, which include the exact fingerprint of the intent it authorizes,
so a permit cannot be forged, stretched to another intent, enlarged or reused.
The OMS calls `PermitLedger.consume` immediately before sending an order.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from threading import Lock
from uuid import UUID, uuid4

from services.execution import RiskPermit, TradeIntent
from services.execution.contracts import MAX_PERMIT_LIFETIME

MIN_KEY_BYTES = 32


class PermitRejected(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _material(
    *,
    permit_id: UUID,
    intent_id: UUID,
    intent_fingerprint: str,
    max_quantity: str,
    issued_at: datetime,
    expires_at: datetime,
) -> bytes:
    return json.dumps(
        {
            "permit_id": str(permit_id),
            "intent_id": str(intent_id),
            "intent_fingerprint": intent_fingerprint,
            "max_quantity": max_quantity,
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


class PermitSigner:
    def __init__(self, key: bytes, *, ttl: timedelta = timedelta(seconds=10)) -> None:
        if len(key) < MIN_KEY_BYTES:
            raise ValueError("permit signing key must be at least 32 bytes")
        if not timedelta(0) < ttl <= MAX_PERMIT_LIFETIME:
            raise ValueError("permit ttl must be positive and at most 60 seconds")
        self._key = key
        self.ttl = ttl

    def _sign(self, **fields) -> str:
        return hmac.new(self._key, _material(**fields), hashlib.sha256).hexdigest()

    def issue(self, intent: TradeIntent, *, now: datetime) -> RiskPermit:
        fields = {
            "permit_id": uuid4(),
            "intent_id": intent.intent_id,
            "intent_fingerprint": intent.fingerprint(),
            "max_quantity": str(intent.quantity),
            "issued_at": now,
            "expires_at": now + self.ttl,
        }
        return RiskPermit(**fields, signature=self._sign(**fields))

    def verify(self, permit: RiskPermit, intent: TradeIntent, *, now: datetime) -> None:
        expected = self._sign(
            permit_id=permit.permit_id,
            intent_id=permit.intent_id,
            intent_fingerprint=permit.intent_fingerprint,
            max_quantity=str(permit.max_quantity),
            issued_at=permit.issued_at,
            expires_at=permit.expires_at,
        )
        if not hmac.compare_digest(expected, permit.signature):
            raise PermitRejected("bad_signature")
        if now < permit.issued_at or now >= permit.expires_at:
            raise PermitRejected("expired")
        if not permit.covers(intent, now=now):
            raise PermitRejected("intent_mismatch")


class PermitLedger:
    """Single-use enforcement. Spent ids are kept until their permits expire."""

    def __init__(self, signer: PermitSigner) -> None:
        self._signer = signer
        self._spent: dict[UUID, datetime] = {}
        self._lock = Lock()

    def verify(self, permit: RiskPermit, intent: TradeIntent, *, now: datetime) -> None:
        self._signer.verify(permit, intent, now=now)

    def consume(self, permit: RiskPermit, intent: TradeIntent, *, now: datetime) -> None:
        self.verify(permit, intent, now=now)
        with self._lock:
            for permit_id, expires_at in list(self._spent.items()):
                if expires_at <= now:
                    del self._spent[permit_id]
            if permit.permit_id in self._spent:
                raise PermitRejected("already_used")
            self._spent[permit.permit_id] = permit.expires_at
