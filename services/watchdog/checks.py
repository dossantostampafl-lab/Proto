"""What the watchdog looks at, and when it trips.

The watchdog is a second line of defence, independent of the Risk Engine: it reads the public API
and the executor from outside, applies the constitutional limits itself, and halts trading when
any check fails. It may only halt. Releasing a halt stays an operator action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from services.execution.constitution import MAX_DAILY_LOSS, MAX_DRAWDOWN, REFERENCE_CAPITAL


@dataclass(frozen=True)
class Limits:
    """Tighter-only thresholds: a fraction above 1 of a constitutional limit is refused."""

    daily_loss_fraction: Decimal = Decimal("1")
    drawdown_fraction: Decimal = Decimal("1")
    # Consecutive failed cycles before an outage trips (a single lost probe is not an outage).
    unreachable_cycles: int = 3
    stale_market_cycles: int = 6
    max_reconcile_age: timedelta = timedelta(minutes=3)

    def __post_init__(self) -> None:
        for name in ("daily_loss_fraction", "drawdown_fraction"):
            value = getattr(self, name)
            if not Decimal("0") < value <= Decimal("1"):
                raise ValueError(f"{name} must be in (0, 1]: the watchdog may only tighten limits")
        if self.unreachable_cycles < 1 or self.stale_market_cycles < 1:
            raise ValueError("cycle counts must be positive")

    @property
    def max_daily_loss(self) -> Decimal:
        return MAX_DAILY_LOSS * self.daily_loss_fraction

    @property
    def max_drawdown(self) -> Decimal:
        return MAX_DRAWDOWN * self.drawdown_fraction


@dataclass(frozen=True)
class Observation:
    """One probe cycle. `None` means the source could not be read."""

    at: datetime
    api_health: bool
    floor: dict[str, Any] | None  # GET /v1/floor/state
    market_ready: bool | None  # GET /live/ready
    executor_configured: bool = False
    executor: dict[str, Any] | None = None  # GET executor /v1/watchdog/status
    # The XP (B3) executor on the operator's PC, over Tailscale: same status route.
    xp_configured: bool = False
    xp: dict[str, Any] | None = None


# B3 regular session (Brasilia), kept here on purpose: the watchdog depends on no Risk Engine code.
_B3_TZ = ZoneInfo("America/Sao_Paulo")
_B3_OPEN, _B3_CLOSE = time(10, 0), time(17, 55)


def _b3_session(at: datetime) -> bool:
    local = at.astimezone(_B3_TZ)
    return local.weekday() < 5 and _B3_OPEN <= local.time() < _B3_CLOSE


def _dec(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


@dataclass
class Checks:
    """Turns observations into trip reasons, counting outages across cycles."""

    limits: Limits = field(default_factory=Limits)
    _misses: dict[str, int] = field(default_factory=dict)

    def _sustained(self, name: str, failing: bool, cycles: int) -> bool:
        self._misses[name] = self._misses.get(name, 0) + 1 if failing else 0
        return self._misses[name] >= cycles

    def evaluate(self, obs: Observation) -> tuple[list[str], dict[str, str]]:
        """Returns (trip reasons, status of every check)."""

        trips: list[str] = []
        status: dict[str, str] = {}
        lim = self.limits

        def check(name: str, failing: bool, *, cycles: int = 1, reason: str | None = None) -> None:
            tripped = self._sustained(name, failing, cycles)
            status[name] = "trip" if tripped else "warn" if failing else "ok"
            if tripped:
                trips.append(reason or name)

        check(
            "api_reachable",
            not obs.api_health or obs.floor is None,
            cycles=lim.unreachable_cycles,
            reason="api_unreachable",
        )

        floor = obs.floor or {}
        if obs.floor is not None:
            journal = str(floor.get("journal", "UNKNOWN"))
            check("journal_integrity", journal != "VALID", reason=f"journal_{journal.lower()}")
            for asset, account in sorted((floor.get("accounts") or {}).items()):
                if not isinstance(account, dict):
                    check(
                        f"account_{asset}",
                        True,
                        cycles=lim.unreachable_cycles,
                        reason=f"account_unavailable:{asset}",
                    )
                    continue
                status[f"account_{asset}"] = "ok"
                self._misses[f"account_{asset}"] = 0
                equity = _dec(account.get("equity"))
                start = _dec(account.get("start_of_day_equity"))
                peak = _dec(account.get("peak_equity"))
                if equity is None or start is None or peak is None:
                    check(
                        f"account_{asset}",
                        True,
                        cycles=lim.unreachable_cycles,
                        reason=f"account_unreadable:{asset}",
                    )
                    continue
                # The account's own reference capital (R$ 10,000 in its quote currency), never
                # looser than the plain number; an API that does not report it keeps the old basis.
                cap = _dec(account.get("reference_capital"))
                cap = min(cap, REFERENCE_CAPITAL) if cap and cap > 0 else REFERENCE_CAPITAL
                if start > 0:
                    check(
                        f"daily_loss_{asset}",
                        (equity - start) / min(start, cap)
                        <= -lim.max_daily_loss,
                        reason=f"daily_loss:{asset}",
                    )
                if peak > 0:
                    check(
                        f"drawdown_{asset}",
                        (equity - peak) / min(peak, cap) <= -lim.max_drawdown,
                        reason=f"drawdown:{asset}",
                    )
            pipeline = floor.get("pipeline") or {}
            executing = (
                bool(pipeline.get("paper_execution_enabled"))
                or pipeline.get("execution_backend") == "executor"
            )
            check(
                "market_data",
                executing and obs.market_ready is not True,
                cycles=lim.stale_market_cycles,
                reason="market_data_stale",
            )

        if obs.executor_configured:
            ex = obs.executor
            check(
                "executor_reachable",
                ex is None or not ex.get("ready"),
                cycles=lim.unreachable_cycles,
                reason="executor_unreachable",
            )
            if ex is not None and ex.get("ready"):
                check(
                    "executor_reconciliation",
                    ex.get("consistent") is False,
                    reason="reconciliation_divergence",
                )
                last = ex.get("last_reconcile_at")
                try:
                    age = obs.at - datetime.fromisoformat(str(last)) if last else None
                except ValueError:
                    age = None
                check(
                    "executor_reconcile_age",
                    age is None or age > lim.max_reconcile_age,
                    reason="reconciliation_stale",
                )
        return trips, status

    def evaluate_xp(self, obs: Observation) -> tuple[list[str], dict[str, str]]:
        """XP (B3) node checks: trips halt the XP executor only, never the API or Binance.

        The node is the operator's PC: off at night and on weekends by design, and the API already
        refuses B3 risk while it is unreachable. So an unreachable node is reported, not tripped.
        A divergence trips at once; a stale reconciliation only during the B3 session, when an
        order could actually be placed.
        """

        if not obs.xp_configured:
            return [], {}
        xp = obs.xp
        if xp is None or not xp.get("ready"):
            return [], {"xp_executor": "unreachable"}
        trips: list[str] = []
        status = {"xp_executor": "halted" if xp.get("halted") else "ok"}
        if xp.get("consistent") is False:
            trips.append("xp_reconciliation_divergence")
        last = xp.get("last_reconcile_at")
        try:
            age = obs.at - datetime.fromisoformat(str(last)) if last else None
        except ValueError:
            age = None
        stale = age is None or age > self.limits.max_reconcile_age
        if stale and _b3_session(obs.at):
            trips.append("xp_reconciliation_stale")
        status["xp_reconciliation"] = "trip" if trips else "ok"
        return trips, status
