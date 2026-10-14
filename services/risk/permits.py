"""Signed, short-lived, single-use Risk Permits.

Only the Risk Engine holds the signing key. A permit signs its own fields,
which include the exact fingerprint of the intent it authorizes, so a permit
cannot be forged, stretched to another intent, enlarged or reused. The OMS
calls `PermitLedger.consume` immediately before sending an order.

Two schemes:

- HMAC-SHA256 (`PermitSigner`): one shared key signs and verifies. Fine when the
  Risk Engine and the OMS live in one process (paper).
- Ed25519 (`Ed25519PermitSigner` / `Ed25519PermitVerifier`): the Risk Engine
  keeps the private key; the isolated executor holds only the public key, so it
  can verify permits but never mint one. Required before any canary scope.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from threading import Lock
from typing import Protocol
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from services.execution import RiskPermit, TradeIntent
from services.execution.contracts import MAX_PERMIT_LIFETIME

MIN_KEY_BYTES = 32
ED25519_PREFIX = "ed25519:"


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
        _check_scope(permit, intent, now=now)


def _check_scope(permit: RiskPermit, intent: TradeIntent, *, now: datetime) -> None:
    if now < permit.issued_at or now >= permit.expires_at:
        raise PermitRejected("expired")
    if not permit.covers(intent, now=now):
        raise PermitRejected("intent_mismatch")


def _permit_material(permit: RiskPermit) -> bytes:
    return _material(
        permit_id=permit.permit_id,
        intent_id=permit.intent_id,
        intent_fingerprint=permit.intent_fingerprint,
        max_quantity=str(permit.max_quantity),
        issued_at=permit.issued_at,
        expires_at=permit.expires_at,
    )


class PermitVerifier(Protocol):
    def verify(self, permit: RiskPermit, intent: TradeIntent, *, now: datetime) -> None: ...


class Ed25519PermitVerifier:
    """Verifies permits with the Risk Engine's public key; cannot issue any."""

    def __init__(self, public_key: bytes) -> None:
        self._key = Ed25519PublicKey.from_public_bytes(public_key)

    def verify(self, permit: RiskPermit, intent: TradeIntent, *, now: datetime) -> None:
        if not permit.signature.startswith(ED25519_PREFIX):
            raise PermitRejected("bad_signature")
        try:
            signature = bytes.fromhex(permit.signature.removeprefix(ED25519_PREFIX))
            self._key.verify(signature, _permit_material(permit))
        except (InvalidSignature, ValueError) as exc:
            raise PermitRejected("bad_signature") from exc
        _check_scope(permit, intent, now=now)


class Ed25519PermitSigner:
    """The Risk Engine's side: holds the 32-byte Ed25519 private seed."""

    def __init__(self, private_key: bytes, *, ttl: timedelta = timedelta(seconds=10)) -> None:
        if len(private_key) != 32:
            raise ValueError("Ed25519 private key must be the 32-byte seed")
        if not timedelta(0) < ttl <= MAX_PERMIT_LIFETIME:
            raise ValueError("permit ttl must be positive and at most 60 seconds")
        self._key = Ed25519PrivateKey.from_private_bytes(private_key)
        self.ttl = ttl
        self.public_key = self._key.public_key().public_bytes_raw()
        self._verifier = Ed25519PermitVerifier(self.public_key)

    def issue(self, intent: TradeIntent, *, now: datetime) -> RiskPermit:
        fields = {
            "permit_id": uuid4(),
            "intent_id": intent.intent_id,
            "intent_fingerprint": intent.fingerprint(),
            "max_quantity": str(intent.quantity),
            "issued_at": now,
            "expires_at": now + self.ttl,
        }
        signature = self._key.sign(_material(**fields)).hex()
        return RiskPermit(**fields, signature=ED25519_PREFIX + signature)

    def verify(self, permit: RiskPermit, intent: TradeIntent, *, now: datetime) -> None:
        self._verifier.verify(permit, intent, now=now)


class PermitLedger:
    """Single-use enforcement. Spent ids are kept until their permits expire."""

    def __init__(self, signer: PermitVerifier) -> None:
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
