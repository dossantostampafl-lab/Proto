from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn


class ExternalExecutionDisabledError(RuntimeError):
    """Raised whenever code attempts to cross the permanent external-execution boundary."""


@dataclass(frozen=True, slots=True)
class ExternalExecutionGate:
    """Fail-closed boundary for any external financial action.

    The research runtime may ingest public/read-only data and may record hypothetical
    decisions, but this gate intentionally exposes no credentials, transport, broker,
    exchange, deposit, withdrawal, custody, leverage, or external-order capability.
    """

    enabled: bool = False

    def submit(self, *_args: object, **_kwargs: object) -> NoReturn:
        raise ExternalExecutionDisabledError("external financial execution is permanently disabled")

    def deposit(self, *_args: object, **_kwargs: object) -> NoReturn:
        raise ExternalExecutionDisabledError("deposits are permanently disabled")

    def withdraw(self, *_args: object, **_kwargs: object) -> NoReturn:
        raise ExternalExecutionDisabledError("withdrawals are permanently disabled")

    def credentials(self) -> NoReturn:
        raise ExternalExecutionDisabledError("trading credentials are not supported")
