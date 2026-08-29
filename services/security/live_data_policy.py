from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit


class LiveDataPolicyViolation(ValueError):
    """Raised when live-data configuration crosses the read-only boundary."""


_PRIVATE_INTEGRATION = re.compile(
    r"(?:EXCHANGE|BROKER|WALLET|TRADING|ORDER_ROUTING).*(?:API_KEY|SECRET|TOKEN|PASSWORD|PRIVATE_KEY|SIGNING_KEY)$"
)
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "private_key",
        "secret",
        "signature",
        "token",
    }
)
_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def validate_no_private_credentials(environment: Mapping[str, str]) -> None:
    """Fail closed when exchange/broker private credentials are configured.

    The match is deliberately scoped to trading integrations so unrelated CI or
    database secrets do not create false positives. Empty variables are ignored.
    """

    forbidden = sorted(
        name
        for name, value in environment.items()
        if value.strip() and _PRIVATE_INTEGRATION.search(name.upper())
    )
    if forbidden:
        names = ", ".join(forbidden)
        raise LiveDataPolicyViolation(
            f"private trading credentials are forbidden in LIVE_DATA_READ_ONLY: {names}"
        )


@dataclass(frozen=True, slots=True)
class LiveDataPolicy:
    """Validates public, allowlisted, read-only live-data access."""

    allowed_hosts: frozenset[str]
    allowed_ports: frozenset[int] = frozenset({443})

    def __post_init__(self) -> None:
        normalized = frozenset(self._normalize_host(host) for host in self.allowed_hosts)
        if not normalized:
            raise LiveDataPolicyViolation("at least one live-data host must be allowlisted")
        if not self.allowed_ports or any(
            port < 1 or port > 65_535 for port in self.allowed_ports
        ):
            raise LiveDataPolicyViolation("live-data ports must be between 1 and 65535")
        object.__setattr__(self, "allowed_hosts", normalized)

    def validate_endpoint(self, endpoint: str) -> SplitResult:
        parsed = urlsplit(endpoint)
        if parsed.scheme.lower() not in {"https", "wss"}:
            raise LiveDataPolicyViolation("live-data endpoints must use https or wss")
        if parsed.username is not None or parsed.password is not None:
            raise LiveDataPolicyViolation("credentials in live-data URLs are forbidden")
        if parsed.hostname is None:
            raise LiveDataPolicyViolation("live-data endpoint must include a host")

        host = self._normalize_host(parsed.hostname)
        if host not in self.allowed_hosts:
            raise LiveDataPolicyViolation(f"live-data host is not allowlisted: {host}")
        port = parsed.port or 443
        if port not in self.allowed_ports:
            raise LiveDataPolicyViolation("live-data endpoint port is not allowlisted")

        query_keys = {
            item.partition("=")[0].lower() for item in parsed.query.split("&") if item
        }
        if query_keys & _SENSITIVE_QUERY_KEYS:
            raise LiveDataPolicyViolation("credentials in live-data query strings are forbidden")
        return parsed

    def validate_http_method(self, method: str) -> None:
        if method.upper() not in _READ_ONLY_METHODS:
            raise LiveDataPolicyViolation(
                f"outbound HTTP method is not read-only: {method.upper()}"
            )

    @staticmethod
    def _normalize_host(host: str) -> str:
        normalized = host.strip().lower().rstrip(".")
        if not normalized or "/" in normalized or "@" in normalized:
            raise LiveDataPolicyViolation("invalid live-data host allowlist entry")
        return normalized
