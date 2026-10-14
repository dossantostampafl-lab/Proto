"""Generate an Ed25519 key pair for Risk Permits.

    python -m services.risk.permit_keys

Put the private seed only in the Risk Engine's secret store
(RISK_PERMIT_PRIVATE_KEY on the public API) and the public key on the
executor (EXECUTOR_PERMIT_PUBLIC_KEY). The executor can then verify permits
but never mint one.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def decode_key(value: str, *, length: int = 32) -> bytes:
    raw = base64.b64decode(value.strip(), validate=True)
    if len(raw) != length:
        raise ValueError(f"expected a base64 {length}-byte key")
    return raw


def generate() -> tuple[str, str]:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes_raw()
    public = key.public_key().public_bytes_raw()
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


if __name__ == "__main__":
    private_b64, public_b64 = generate()
    print(f"RISK_PERMIT_PRIVATE_KEY={private_b64}")
    print(f"EXECUTOR_PERMIT_PUBLIC_KEY={public_b64}")
