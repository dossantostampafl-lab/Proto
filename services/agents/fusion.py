"""Deterministic signal fusion and candidate-intent sizing.

Fusion turns recent signals for one instrument into a direction and a
conviction score; sizing turns that into a candidate `TradeIntent` that fits
the constitutional per-position risk budget, spot-only gross leverage and the
venue grid. Nothing here can submit an order: candidates still need an agent
review (which may only shrink them) and, later, a Risk Engine permit.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal
from uuid import UUID, uuid4

from services.execution import (
    OrderType,
    ReleaseStage,
    Signal,
    SignalDirection,
    SignalSource,
    TimeInForce,
    TradableInstrument,
    TradeIntent,
)
from services.execution.constitution import (
    MAX_GROSS_LEVERAGE,
    MAX_RISK_PER_POSITION,
    conservative_equity,
)
from services.execution.venue import Quote

DEFAULT_SOURCE_WEIGHTS: Mapping[SignalSource, float] = {
    SignalSource.TRADINGVIEW: 1.0,
    SignalSource.PROTO_STRATEGY: 1.0,
    SignalSource.PROTO_AGENT: 0.5,
}


@dataclass(frozen=True)
class FusionPolicy:
    window: timedelta = timedelta(minutes=5)
    min_score: float = 0.6
    # Selling on spot only closes a holding (a short cannot be opened), so an exit needs a reason,
    # not the conviction an entry does. With the same bar for both, a position opened on a strong
    # cross almost never closed: weaker opposite crosses were discarded and nothing else exits.
    exit_min_score: float = 0.2
    min_signals: int = 1
    # Opposing signals at least this strong make the result FLAT ("conflict").
    conflict_strength: float = 0.5
    source_weights: Mapping[SignalSource, float] = field(
        default_factory=lambda: dict(DEFAULT_SOURCE_WEIGHTS)
    )

    def __post_init__(self) -> None:
        if not 0 < self.min_score <= 1 or self.min_signals < 1:
            raise ValueError("min_score must be in (0, 1] and min_signals >= 1")
        if not 0 < self.exit_min_score <= self.min_score:
            raise ValueError("exit_min_score must be in (0, min_score]")
        if self.window <= timedelta(0):
            raise ValueError("window must be positive")


DEFAULT_FUSION_POLICY = FusionPolicy()


@dataclass(frozen=True)
class FusionDecision:
    instrument_id: str
    direction: SignalDirection
    score: float  # signed, in [-1, 1]
    signal_ids: tuple[UUID, ...]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "instrument_id": self.instrument_id,
            "direction": self.direction.value,
            "score": round(self.score, 6),
            "signal_ids": [str(item) for item in self.signal_ids],
            "reason": self.reason,
        }


_SIGN = {SignalDirection.LONG: 1.0, SignalDirection.SHORT: -1.0, SignalDirection.FLAT: 0.0}


def fuse(
    signals: Iterable[Signal],
    *,
    instrument_id: str,
    now: datetime,
    policy: FusionPolicy = DEFAULT_FUSION_POLICY,
) -> FusionDecision:
    """Weighted vote over the latest live signal of each (source, strategy)."""

    latest: dict[tuple[SignalSource, str], Signal] = {}
    for signal in signals:
        if signal.instrument_id != instrument_id:
            continue
        if signal.observed_at > now or signal.received_at < now - policy.window:
            continue
        key = (signal.source, signal.strategy_id)
        current = latest.get(key)
        if current is None or signal.observed_at > current.observed_at:
            latest[key] = signal

    live = [s for s in latest.values() if policy.source_weights.get(s.source, 0.0) > 0]
    ids = tuple(sorted((s.signal_id for s in live), key=str))
    if len(live) < policy.min_signals:
        return FusionDecision(instrument_id, SignalDirection.FLAT, 0.0, ids, "not enough signals")

    strong_long = any(
        s.direction is SignalDirection.LONG and s.strength >= policy.conflict_strength for s in live
    )
    strong_short = any(
        s.direction is SignalDirection.SHORT and s.strength >= policy.conflict_strength
        for s in live
    )
    if strong_long and strong_short:
        return FusionDecision(instrument_id, SignalDirection.FLAT, 0.0, ids, "conflicting signals")

    total_weight = sum(policy.source_weights[s.source] for s in live)
    score = (
        sum(_SIGN[s.direction] * s.strength * policy.source_weights[s.source] for s in live)
        / total_weight
    )
    if score >= policy.min_score:
        return FusionDecision(instrument_id, SignalDirection.LONG, score, ids, "consensus long")
    if score <= -policy.exit_min_score:
        return FusionDecision(instrument_id, SignalDirection.SHORT, score, ids, "consensus short")
    return FusionDecision(instrument_id, SignalDirection.FLAT, score, ids, "below min_score")


@dataclass(frozen=True)
class SizingPolicy:
    # Distance to the protective exit used to translate risk into quantity.
    stop_distance: Decimal = Decimal("0.02")
    max_quote_age: timedelta = timedelta(seconds=10)
    strategy_id: str = "hybrid-fusion-v1"
    model_version: str = "fusion-v1"

    def __post_init__(self) -> None:
        if not Decimal("0") < self.stop_distance < Decimal("1"):
            raise ValueError("stop_distance must be in (0, 1)")


DEFAULT_SIZING_POLICY = SizingPolicy()


@dataclass(frozen=True)
class Candidate:
    intent: TradeIntent | None
    abstain_reason: str | None
    sizing: dict[str, str]


def _floor_to_lot(quantity: Decimal, instrument: TradableInstrument) -> Decimal:
    lots = (quantity / instrument.lot_size).to_integral_value(rounding=ROUND_DOWN)
    return lots * instrument.lot_size


def _quote_problem(
    quote: Quote | None, now: datetime, policy: SizingPolicy
) -> tuple[str, dict[str, object]] | None:
    if quote is None:
        return "no market data", {}
    if now - quote.observed_at > policy.max_quote_age:
        return "stale market data", {"quote_age_s": (now - quote.observed_at).total_seconds()}
    if not (0 < quote.bid < quote.ask):
        return "crossed or invalid quote", {}
    return None


def build_candidate(
    decision: FusionDecision,
    *,
    instrument: TradableInstrument,
    quote: Quote | None,
    equity: Decimal,
    position_quantity: Decimal,
    now: datetime,
    policy: SizingPolicy = DEFAULT_SIZING_POLICY,
    stage: ReleaseStage = ReleaseStage.PAPER,
) -> Candidate:
    """Size a spot candidate: BUY on LONG, reduce-only SELL of holdings on SHORT."""

    def abstain(reason: str, **sizing: object) -> Candidate:
        return Candidate(None, reason, {k: str(v) for k, v in sizing.items()})

    if decision.direction is SignalDirection.FLAT:
        return abstain(decision.reason)
    if (problem := _quote_problem(quote, now, policy)) is not None:
        return abstain(problem[0], **problem[1])
    assert quote is not None

    conviction = Decimal(str(min(abs(decision.score), 1.0)))
    if decision.direction is SignalDirection.SHORT:
        if position_quantity <= 0:
            return abstain("spot venue cannot open a short")
        side, price = "SELL", quote.bid
        quantity = _floor_to_lot(position_quantity, instrument)
        sizing: dict[str, object] = {"position_quantity": position_quantity}
    else:
        side, price = "BUY", quote.ask
        sizing_equity = conservative_equity(equity)
        risk_budget = sizing_equity * MAX_RISK_PER_POSITION * conviction
        risk_quantity = risk_budget / (price * policy.stop_distance)
        exposure = max(position_quantity, Decimal("0")) * price
        headroom = sizing_equity * MAX_GROSS_LEVERAGE - exposure
        if headroom <= 0:
            return abstain("gross leverage limit reached", exposure=exposure)
        quantity = _floor_to_lot(min(risk_quantity, headroom / price), instrument)
        sizing = {
            "equity": sizing_equity,
            "risk_budget": risk_budget,
            "stop_distance": policy.stop_distance,
            "conviction": conviction,
            "leverage_headroom": headroom,
        }

    rounding = ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    limit_price = (price / instrument.tick_size).to_integral_value(rounding) * instrument.tick_size
    violation = instrument.order_violation(quantity, limit_price)
    if violation is not None:
        return abstain(violation, quantity=quantity, **sizing)

    intent = TradeIntent(
        intent_id=uuid4(),
        instrument_id=instrument.instrument_id,
        venue=instrument.venue,
        side=side,
        quantity=quantity,
        order_type=OrderType.LIMIT,
        limit_price=limit_price,
        time_in_force=TimeInForce.IOC,
        reduce_only=side == "SELL",
        strategy_id=policy.strategy_id,
        model_version=policy.model_version,
        signal_ids=decision.signal_ids,
        stage=stage,
        created_at=now,
    )
    return Candidate(intent, None, {k: str(v) for k, v in sizing.items()})


def build_exit(
    *,
    instrument: TradableInstrument,
    quote: Quote | None,
    quantity: Decimal,
    position_quantity: Decimal,
    now: datetime,
    signal_ids: tuple[UUID, ...],
    policy: SizingPolicy = DEFAULT_SIZING_POLICY,
    stage: ReleaseStage = ReleaseStage.PAPER,
) -> Candidate:
    """A reduce-only SELL of at most ``quantity``: never more than ``position_quantity``.

    The quantity is floored to the lot and priced at the bid (floored to the tick) as an IOC
    limit, so it can only close part or all of a holding, never open a short. It still needs a
    Risk Engine permit like any other intent; ``signal_ids`` name the signals behind the exit.
    """

    def abstain(reason: str, **sizing: object) -> Candidate:
        return Candidate(None, reason, {k: str(v) for k, v in sizing.items()})

    requested = max(quantity, Decimal("0"))
    held = max(position_quantity, Decimal("0"))
    sizing: dict[str, object] = {"requested_quantity": requested, "position_quantity": held}
    if requested == 0:
        return abstain("nothing to close", **sizing)
    if held == 0:
        return abstain("no position to close", **sizing)
    if (problem := _quote_problem(quote, now, policy)) is not None:
        return abstain(problem[0], **problem[1], **sizing)
    assert quote is not None
    exit_quantity = _floor_to_lot(min(requested, held), instrument)
    limit_price = (quote.bid / instrument.tick_size).to_integral_value(
        ROUND_FLOOR
    ) * instrument.tick_size
    violation = instrument.order_violation(exit_quantity, limit_price)
    if exit_quantity <= 0 or violation is not None:
        return abstain(violation or "below one lot", quantity=exit_quantity, **sizing)
    intent = TradeIntent(
        intent_id=uuid4(),
        instrument_id=instrument.instrument_id,
        venue=instrument.venue,
        side="SELL",
        quantity=exit_quantity,
        order_type=OrderType.LIMIT,
        limit_price=limit_price,
        time_in_force=TimeInForce.IOC,
        reduce_only=True,
        strategy_id=policy.strategy_id,
        model_version=policy.model_version,
        signal_ids=signal_ids,
        stage=stage,
        created_at=now,
    )
    return Candidate(
        intent, None, {k: str(v) for k, v in {**sizing, "quantity": exit_quantity}.items()}
    )
