"""Deterministic Risk Engine: the only component that can authorize an order.

Pure function of (intent, account state, controls, quote, clock). No LLM, no
I/O. Every check runs and is reported; any failure rejects. Risk-reducing
orders (reduce-only sells within the position) stay allowed during a loss or
drawdown lockdown so the book can always be de-risked, but never under a kill
switch, which halts everything.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from services.execution import ReleaseStage, RiskPermit, TradableInstrument, TradeIntent
from services.execution.constitution import (
    MAX_DAILY_LOSS,
    MAX_DRAWDOWN,
    MAX_GROSS_LEVERAGE,
    MAX_RISK_PER_POSITION,
    conservative_equity,
)
from services.execution.venue import Quote
from services.market_data.instruments import SessionType

from .permits import PermitSigner

_BPS = Decimal("10000")
CERTIFIED_STAGES = frozenset({ReleaseStage.PAPER, ReleaseStage.SHADOW})


class LockdownReason(StrEnum):
    DAILY_LOSS = "DAILY_LOSS"
    DRAWDOWN = "DRAWDOWN"


@dataclass(frozen=True)
class RiskPolicy:
    """Operational parameters. Constitutional limits are not configurable here."""

    stop_distance: Decimal = Decimal("0.02")
    max_quote_age: timedelta = timedelta(seconds=10)
    price_collar_bps: Decimal = Decimal("50")
    # Optional per-venue cap on a single order's notional; may only tighten.
    max_order_notional: Mapping[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not Decimal("0") < self.stop_distance < Decimal("1"):
            raise ValueError("stop_distance must be in (0, 1)")
        if not Decimal("0") < self.price_collar_bps <= Decimal("500"):
            raise ValueError("price_collar_bps must be in (0, 500]")
        if self.max_quote_age <= timedelta(0):
            raise ValueError("max_quote_age must be positive")


@dataclass(frozen=True)
class AccountState:
    """Conservative account view supplied by the ledger (Phase 4)."""

    equity: Decimal
    start_of_day_equity: Decimal
    peak_equity: Decimal
    gross_exposure: Decimal
    positions: Mapping[str, Decimal]  # instrument_id -> base quantity (spot, >= 0)


@dataclass(frozen=True)
class Controls:
    global_kill: bool = False
    killed_venues: frozenset[str] = frozenset()
    killed_strategies: frozenset[str] = frozenset()
    lockdown: LockdownReason | None = None


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    permit: RiskPermit | None
    rejections: tuple[str, ...]
    checks: dict[str, str]
    breach: LockdownReason | None  # newly observed breach the governor must latch

    def to_dict(self) -> dict[str, object]:
        return {
            "approved": self.approved,
            "permit_id": str(self.permit.permit_id) if self.permit else None,
            "permit_expires_at": self.permit.expires_at.isoformat() if self.permit else None,
            "rejections": list(self.rejections),
            "checks": self.checks,
            "breach": self.breach.value if self.breach else None,
        }


_B3_OPEN, _B3_CLOSE = time(10, 0), time(17, 55)


def session_open(instrument: TradableInstrument, now: datetime) -> bool:
    """Continuous venues are always open; exchange sessions use regular hours.

    Exchange holidays are not modelled; the venue rejects orders on closed days
    and the OMS treats that rejection as final.
    """

    if instrument.session_type is SessionType.CONTINUOUS_24_7:
        return True
    local = now.astimezone(ZoneInfo(instrument.timezone))
    return local.weekday() < 5 and _B3_OPEN <= local.time() < _B3_CLOSE


def account_breach(account: AccountState) -> tuple[LockdownReason | None, Decimal, Decimal]:
    """Daily loss and drawdown as fractions; unknown baselines count as breached."""

    sod = account.start_of_day_equity
    daily_loss = (sod - account.equity) / sod if sod > 0 else Decimal("1")
    peak = account.peak_equity
    drawdown = (peak - account.equity) / peak if peak > 0 else Decimal("1")
    if daily_loss >= MAX_DAILY_LOSS:
        return LockdownReason.DAILY_LOSS, daily_loss, drawdown
    if drawdown >= MAX_DRAWDOWN:
        return LockdownReason.DRAWDOWN, daily_loss, drawdown
    return None, daily_loss, drawdown


class RiskEngine:
    def __init__(
        self,
        *,
        instruments: Mapping[str, TradableInstrument],
        signer: PermitSigner,
        policy: RiskPolicy | None = None,
    ) -> None:
        self._instruments = dict(instruments)
        self._signer = signer
        self.policy = policy or RiskPolicy()

    @staticmethod
    def evaluate_breach(account: AccountState) -> LockdownReason | None:
        return account_breach(account)[0]

    def evaluate(
        self,
        intent: TradeIntent,
        *,
        account: AccountState,
        controls: Controls,
        quote: Quote | None,
        now: datetime,
    ) -> RiskDecision:
        checks: dict[str, str] = {}
        rejections: list[str] = []

        def check(name: str, ok: bool, detail: str = "") -> None:
            checks[name] = "ok" if ok else f"fail{': ' + detail if detail else ''}"
            if not ok:
                rejections.append(name)

        policy = self.policy
        instrument = self._instruments.get(intent.instrument_id)
        position = max(account.positions.get(intent.instrument_id, Decimal("0")), Decimal("0"))
        reducing = intent.side == "SELL" and intent.reduce_only and intent.quantity <= position

        # Halts and scope.
        check("stage_certified", intent.stage in CERTIFIED_STAGES, intent.stage.value)
        check("global_kill_switch", not controls.global_kill)
        check("venue_kill_switch", intent.venue.value not in controls.killed_venues)
        check("strategy_kill_switch", intent.strategy_id not in controls.killed_strategies)
        check("instrument_known", instrument is not None and instrument.venue is intent.venue)
        check(
            "spot_no_short",
            intent.side == "BUY" or intent.quantity <= position,
            f"sell {intent.quantity} > position {position}",
        )
        if intent.side == "SELL":
            check("sell_is_reduce_only", intent.reduce_only)

        # Market data and price sanity.
        quote_ok = (
            quote is not None
            and 0 < quote.bid < quote.ask
            and timedelta(0) <= now - quote.observed_at <= policy.max_quote_age
        )
        check("fresh_quote", quote_ok)
        price = intent.limit_price
        if quote_ok and quote is not None:
            touch = quote.ask if intent.side == "BUY" else quote.bid
            collar = touch * policy.price_collar_bps / _BPS
            if price is None:
                price = touch
            within = price <= touch + collar if intent.side == "BUY" else price >= touch - collar
            check("price_collar", within, f"{price} vs touch {touch}")
        if instrument is not None:
            check("session_open", session_open(instrument, now))
            if price is not None:
                violation = instrument.order_violation(intent.quantity, price)
                check("venue_grid", violation is None, violation or "")

        # Account-level constitutional limits.
        breach, daily_loss, drawdown = account_breach(account)
        lockdown = controls.lockdown or breach
        checks["daily_loss"] = f"{daily_loss:.6f}"
        checks["drawdown"] = f"{drawdown:.6f}"
        if not reducing:
            check("no_lockdown", lockdown is None, lockdown.value if lockdown else "")
            equity = conservative_equity(account.equity)
            notional = intent.quantity * (price or Decimal("0"))
            budget = equity * MAX_RISK_PER_POSITION
            check(
                "risk_per_position",
                notional * policy.stop_distance <= budget,
                f"{notional * policy.stop_distance:.2f} > {budget:.2f}",
            )
            check(
                "gross_leverage",
                account.gross_exposure + notional <= equity * MAX_GROSS_LEVERAGE,
                f"{account.gross_exposure + notional:.2f} > {equity * MAX_GROSS_LEVERAGE:.2f}",
            )
            venue_cap = policy.max_order_notional.get(intent.venue.value)
            if venue_cap is not None:
                check("venue_order_notional", notional <= venue_cap)

        approved = not rejections
        permit = self._signer.issue(intent, now=now) if approved else None
        return RiskDecision(approved, permit, tuple(rejections), checks, breach)
