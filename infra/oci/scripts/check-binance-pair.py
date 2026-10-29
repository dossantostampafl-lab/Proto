#!/usr/bin/env python3
"""Refuse a Binance testnet pair that cannot sign, before the executor ever loads it.

    proto-check-binance-pair <executor.env being written>

Run by fetch-secrets.sh on the executor host, on the freshly read file and before it replaces
the one in use. Exit 0 when the pair looks right (or Binance cannot be reached to say), exit 3
when it is surely wrong: the executor then keeps the pair it already has.

Checks, never printing a value:
* both halves are 64 letters and digits (Binance HMAC keys and secrets);
* the secret is not the Alpaca paper secret ($PROTO_ALPACA_SECRET, when readable): on
  2026-10-28 a new version of proto-binance-testnet-secret held the Alpaca secret by mistake,
  the executor restarted on it and every request failed with -1022;
* a signed GET /api/v3/account on the testnet is not refused for the key or the signature.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

KEY = "PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_KEY"
SECRET = "PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_SECRET"
HMAC_TOKEN = re.compile(r"^[A-Za-z0-9]{64}$")
# Key or signature refused: the pair itself is wrong (not a network or rate problem).
REFUSED = {-1022, -2014, -2015}


def read_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    with open(path) as handle:
        for line in handle:
            if "=" in line:
                name, value = line.rstrip("\n").split("=", 1)
                values[name] = value
    return values


def problem(key: str, secret: str, alpaca_secret: str, base_url: str) -> str | None:
    """Why the pair is surely wrong, or None."""

    if not HMAC_TOKEN.match(key):
        return f"the API key has {len(key)} characters, not 64 letters and digits"
    if not HMAC_TOKEN.match(secret):
        same = (
            " and it is the Alpaca paper secret"
            if alpaca_secret and secret == alpaca_secret
            else ""
        )
        return f"the secret has {len(secret)} characters, not 64 letters and digits{same}"
    if alpaca_secret and secret == alpaca_secret:
        return "the secret is the Alpaca paper secret"
    if key == secret:
        return "the API key and the secret are the same value"
    query = f"timestamp={int(time.time() * 1000)}&recvWindow=10000"
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        f"{base_url}/api/v3/account?{query}&signature={signature}", headers={"X-MBX-APIKEY": key}
    )
    try:
        urllib.request.urlopen(request, timeout=15)
    except urllib.error.HTTPError as exc:
        try:
            code = json.loads(exc.read() or b"{}").get("code")
        except ValueError:
            code = None
        if code in REFUSED:
            return f"Binance testnet refuses the pair (code {code})"
        print(f"binance pair: not verified, the testnet answered HTTP {exc.code}", file=sys.stderr)
    except OSError as exc:
        print(
            f"binance pair: not verified, testnet unreachable ({type(exc).__name__})",
            file=sys.stderr,
        )
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: proto-check-binance-pair <executor.env>", file=sys.stderr)
        return 2
    values = read_env(argv[1])
    found = problem(
        values.get(KEY, ""),
        values.get(SECRET, ""),
        os.environ.get("PROTO_ALPACA_SECRET", ""),
        os.environ.get("PROTO_BINANCE_TESTNET_URL", "https://testnet.binance.vision"),
    )
    if found:
        print(f"BINANCE PAIR REFUSED: {found}; the executor keeps the pair it has", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
