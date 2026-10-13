"""Stateful wrapper around the Risk Engine: kill switches and the lockdown latch.

Automation can only make the state stricter: triggering a kill switch or
latching a lockdown happens automatically, but every release is an explicit
operator action, and a lockdown cannot be released while its breach persists.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from threading import Lock

from services.execution import TradeIntent
from services.execution.venue import Quote

from .engine import AccountState, Controls, LockdownReason, RiskDecision, RiskEngine


class KillScope(StrEnum):
    GLOBAL = "GLOBAL"
    VENUE = "VENUE"
    STRATEGY = "STRATEGY"


class GovernorError(ValueError):
    pass


class RiskGovernor:
    def __init__(self, engine: RiskEngine) -> None:
        self.engine = engine
        self._global = False
        self._venues: set[str] = set()
        self._strategies: set[str] = set()
        self._lockdown: LockdownReason | None = None
        self._lock = Lock()

    def controls(self, *, external_global_kill: bool = False) -> Controls:
        with self._lock:
            return Controls(
                global_kill=self._global or external_global_kill,
                killed_venues=frozenset(self._venues),
                killed_strategies=frozenset(self._strategies),
                lockdown=self._lockdown,
            )

    def assess(
        self,
        intent: TradeIntent,
        *,
        account: AccountState,
        quote: Quote | None,
        now: datetime,
        external_global_kill: bool = False,
    ) -> RiskDecision:
        decision = self.engine.evaluate(
            intent,
            account=account,
            controls=self.controls(external_global_kill=external_global_kill),
            quote=quote,
            now=now,
        )
        if decision.breach is not None:
            with self._lock:
                if self._lockdown is None:
                    self._lockdown = decision.breach
        return decision

    def trigger_kill(self, scope: KillScope, key: str | None = None) -> None:
        with self._lock:
            self._target(scope, key, add=True)

    def release_kill(self, scope: KillScope, key: str | None = None) -> None:
        """Operator-only."""

        with self._lock:
            self._target(scope, key, add=False)

    def release_lockdown(self, account: AccountState) -> None:
        """Operator-only; refused while the account still breaches a limit."""

        probe = self.engine.evaluate_breach(account)
        if probe is not None:
            raise GovernorError(f"{probe.value} limit is still breached")
        with self._lock:
            self._lockdown = None

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "global_kill": self._global,
                "killed_venues": sorted(self._venues),
                "killed_strategies": sorted(self._strategies),
                "lockdown": self._lockdown.value if self._lockdown else None,
            }

    def _target(self, scope: KillScope, key: str | None, *, add: bool) -> None:
        if scope is KillScope.GLOBAL:
            self._global = add
            return
        if not key:
            raise GovernorError(f"{scope.value} kill switch needs a key")
        bucket = self._venues if scope is KillScope.VENUE else self._strategies
        if add:
            bucket.add(key)
        else:
            bucket.discard(key)
