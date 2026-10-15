"""Print a new Ed25519 permit key pair as "<private seed b64> <public key b64>".

Same format as ``python -m services.risk.permit_keys``, but standard library only: Cloud Shell
runs in FIPS mode, where OpenSSL (and so ``cryptography``) refuses Ed25519. The public key is
derived from the seed per RFC 8032 section 5.1.5; signing never happens here.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

P = 2**255 - 19
D = -121665 * pow(121666, P - 2, P) % P
GY = 4 * pow(5, P - 2, P) % P


def _recover_x(y: int, sign: int) -> int:
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * pow(2, (P - 1) // 4, P) % P
    if x & 1 != sign:
        x = P - x
    return x


def _add(a: tuple, b: tuple) -> tuple:
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    aa = (y1 - x1) * (y2 - x2) % P
    bb = (y1 + x1) * (y2 + x2) % P
    cc = 2 * t1 * t2 * D % P
    dd = 2 * z1 * z2 % P
    e, f, g, h = bb - aa, dd - cc, dd + cc, bb + aa
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(s: int, point: tuple) -> tuple:
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, point)
        point = _add(point, point)
        s >>= 1
    return q


def public_key(seed: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    gx = _recover_x(GY, 0)
    x, y, z, _ = _mul(a, (gx, GY, 1, gx * GY % P))
    zi = pow(z, P - 2, P)
    x, y = x * zi % P, y * zi % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


if __name__ == "__main__":
    seed = secrets.token_bytes(32)
    print(base64.b64encode(seed).decode(), base64.b64encode(public_key(seed)).decode())
