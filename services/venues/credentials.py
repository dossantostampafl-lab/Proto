"""Venue credentials for the isolated executor only.

Credentials are scoped by venue, environment and account, never printed and
never handed to agents, prompts, the public API or the frontend. Only the
executor process imports this module (enforced by `tests/unit/test_venue_isolation.py`).

Lookup for venue `BINANCE_SPOT`, environment `TESTNET`, account `main`:

    PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_KEY        (or ..._API_KEY_FILE)
    PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_SECRET     (or ..._API_SECRET_FILE)

The `_FILE` form is what the OCI Vault fetch at executor start writes to a
tmpfs mount, so secrets never sit in the process environment of other services.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from services.execution import VenueId

_SCOPE = re.compile(r"^[A-Z0-9_]{1,40}$")


class VenueEnvironment(StrEnum):
    """TESTNET holds no real money. LIVE needs a certified scope (Phase 6)."""

    TESTNET = "TESTNET"
    LIVE = "LIVE"


class CredentialsUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class VenueCredentials:
    venue: VenueId
    environment: VenueEnvironment
    account: str
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.api_key or not self.api_secret:
            raise CredentialsUnavailable("api key and secret are required")

    def __str__(self) -> str:
        return f"{self.venue.value}/{self.environment.value}/{self.account} (redacted)"


def _prefix(venue: VenueId, environment: VenueEnvironment, account: str) -> str:
    account_key = account.upper().replace("-", "_")
    if not _SCOPE.match(account_key):
        raise ValueError("account must be alphanumeric, '-' or '_'")
    return f"PROTO_CRED_{venue.value}_{environment.value}_{account_key}"


def _read(name: str, env: Mapping[str, str]) -> str:
    path = env.get(f"{name}_FILE")
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise CredentialsUnavailable(f"{name}_FILE is not readable") from exc
    value = env.get(name, "").strip()
    if not value:
        raise CredentialsUnavailable(f"{name} is not set")
    return value


def load_credentials(
    venue: VenueId,
    environment: VenueEnvironment,
    account: str,
    *,
    env: Mapping[str, str] | None = None,
) -> VenueCredentials:
    source = os.environ if env is None else env
    prefix = _prefix(venue, environment, account)
    return VenueCredentials(
        venue=venue,
        environment=environment,
        account=account,
        api_key=_read(f"{prefix}_API_KEY", source),
        api_secret=_read(f"{prefix}_API_SECRET", source),
    )
