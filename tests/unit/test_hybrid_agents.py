from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from services.agents import (
    AgentReview,
    ClaudeReviewer,
    DecisionOutcome,
    DeterministicReviewer,
    FusionPolicy,
    HybridDecisionPipeline,
    PortfolioView,
    ReplayCase,
    ReviewContext,
    SizingPolicy,
    Verdict,
    apply_review,
    build_candidate,
    evaluate_reviewer,
    fuse,
)
from services.agents.review import REVIEW_SCHEMA
from services.execution import (
    ReleaseStage,
    Signal,
    SignalDirection,
    SignalSource,
    TradableInstrument,
    VenueId,
)
from services.execution.venue import Quote
from services.market_data.instruments import AssetClass, SessionType

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
BTC_ID = "BINANCE_SPOT:BTCBRL"
BTC = TradableInstrument(
    instrument_id=BTC_ID,
    venue=VenueId.BINANCE_SPOT,
    asset_class=AssetClass.CRYPTO,
    base_asset="BTC",
    quote_asset="BRL",
    tick_size=Decimal("1"),
    lot_size=Decimal("0.00001"),
    min_quantity=Decimal("0.00001"),
    min_notional=Decimal("10"),
    session_type=SessionType.CONTINUOUS_24_7,
    timezone="UTC",
)
QUOTE = Quote(bid=Decimal("349990"), ask=Decimal("350010"), observed_at=NOW - timedelta(seconds=1))


def signal(
    direction: SignalDirection = SignalDirection.LONG,
    *,
    strength: float = 0.8,
    source: SignalSource = SignalSource.TRADINGVIEW,
    strategy: str = "tv-ema",
    age: timedelta = timedelta(seconds=10),
    instrument_id: str = BTC_ID,
) -> Signal:
    return Signal(
        signal_id=uuid4(),
        source=source,
        strategy_id=strategy,
        instrument_id=instrument_id,
        direction=direction,
        strength=strength,
        timeframe="15",
        observed_at=NOW - age,
        received_at=NOW - age,
    )


# --- fusion -------------------------------------------------------------------


def test_no_signals_means_no_trade() -> None:
    decision = fuse([], instrument_id=BTC_ID, now=NOW)
    assert decision.direction is SignalDirection.FLAT
    assert decision.reason == "not enough signals"


def test_single_strong_signal_produces_a_direction() -> None:
    long = fuse([signal()], instrument_id=BTC_ID, now=NOW)
    assert long.direction is SignalDirection.LONG
    assert long.score == pytest.approx(0.8)
    short = fuse([signal(SignalDirection.SHORT, strength=0.9)], instrument_id=BTC_ID, now=NOW)
    assert short.direction is SignalDirection.SHORT


def test_weak_or_diluted_consensus_stays_flat() -> None:
    weak = fuse([signal(strength=0.4)], instrument_id=BTC_ID, now=NOW)
    assert weak.direction is SignalDirection.FLAT
    diluted = fuse(
        [signal(), signal(SignalDirection.FLAT, strategy="tv-rsi")],
        instrument_id=BTC_ID,
        now=NOW,
    )
    assert diluted.direction is SignalDirection.FLAT
    assert diluted.score == pytest.approx(0.4)


def test_strong_opposing_signals_are_a_conflict() -> None:
    decision = fuse(
        [signal(), signal(SignalDirection.SHORT, strength=0.6, strategy="proto-imbalance")],
        instrument_id=BTC_ID,
        now=NOW,
    )
    assert decision.direction is SignalDirection.FLAT
    assert decision.reason == "conflicting signals"


def test_stale_future_and_foreign_signals_are_ignored() -> None:
    decision = fuse(
        [
            signal(age=timedelta(minutes=6)),
            signal(age=timedelta(seconds=-5)),
            signal(instrument_id="BINANCE_SPOT:ETHBRL"),
        ],
        instrument_id=BTC_ID,
        now=NOW,
    )
    assert decision.signal_ids == ()


def test_latest_signal_per_strategy_supersedes_older_ones() -> None:
    old_long = signal(age=timedelta(seconds=60))
    new_flat = signal(SignalDirection.FLAT, age=timedelta(seconds=5))
    decision = fuse([old_long, new_flat], instrument_id=BTC_ID, now=NOW)
    assert decision.direction is SignalDirection.FLAT
    assert decision.signal_ids == (new_flat.signal_id,)


def test_agent_signals_carry_less_weight() -> None:
    decision = fuse(
        [
            signal(strength=0.9),
            signal(
                SignalDirection.SHORT,
                strength=0.4,
                source=SignalSource.PROTO_AGENT,
                strategy="news",
            ),
        ],
        instrument_id=BTC_ID,
        now=NOW,
    )
    # (0.9*1 - 0.4*0.5) / 1.5
    assert decision.score == pytest.approx(0.4666666, rel=1e-5)


def test_fusion_policy_validates_its_bounds() -> None:
    with pytest.raises(ValueError):
        FusionPolicy(min_score=0)
    with pytest.raises(ValueError):
        FusionPolicy(exit_min_score=0)
    with pytest.raises(ValueError):
        FusionPolicy(min_score=0.5, exit_min_score=0.6)  # an exit may not be stricter


def test_a_weak_opposite_cross_closes_but_never_opens() -> None:
    """Spot selling only closes a holding, so it needs a reason; buying needs conviction."""

    weak_short = fuse(
        [signal(SignalDirection.SHORT, strength=0.3)], instrument_id=BTC_ID, now=NOW
    )
    weak_long = fuse([signal(SignalDirection.LONG, strength=0.3)], instrument_id=BTC_ID, now=NOW)
    assert weak_short.direction is SignalDirection.SHORT and weak_short.reason == "consensus short"
    assert weak_long.direction is SignalDirection.FLAT and weak_long.reason == "below min_score"


def test_a_very_weak_cross_does_not_even_exit() -> None:
    faint = fuse([signal(SignalDirection.SHORT, strength=0.1)], instrument_id=BTC_ID, now=NOW)
    assert faint.direction is SignalDirection.FLAT


def test_a_short_decision_cannot_open_a_position_on_spot() -> None:
    """The lower exit bar is safe because sizing still refuses a short without holdings."""

    decision = fuse([signal(SignalDirection.SHORT, strength=0.3)], instrument_id=BTC_ID, now=NOW)
    candidate = build_candidate(
        decision,
        instrument=BTC,
        quote=QUOTE,
        equity=Decimal("10000"),
        position_quantity=Decimal("0"),
        now=NOW,
    )
    assert candidate.intent is None and candidate.abstain_reason == "spot venue cannot open a short"
    held = build_candidate(
        decision,
        instrument=BTC,
        quote=QUOTE,
        equity=Decimal("10000"),
        position_quantity=Decimal("0.5"),
        now=NOW,
    )
    assert held.intent is not None and held.intent.side == "SELL"
    assert held.intent.quantity <= Decimal("0.5")  # reduce-only: never more than it holds


# --- sizing -------------------------------------------------------------------


def long_decision(score: float = 0.8):
    return fuse([signal(strength=score)], instrument_id=BTC_ID, now=NOW)


def candidate(**overrides):
    values = {
        "instrument": BTC,
        "quote": QUOTE,
        "equity": Decimal("10000"),
        "position_quantity": Decimal("0"),
        "now": NOW,
    }
    values.update(overrides)
    return build_candidate(values.pop("decision", long_decision()), **values)


def test_long_is_sized_to_the_constitutional_risk_budget() -> None:
    result = candidate()
    intent = result.intent
    assert intent is not None
    # 10000 * 0.25% * 0.8 conviction = 20 BRL risk; / (350010 * 2% stop) = 0.002857 BTC
    assert intent.quantity == Decimal("0.00285")
    assert intent.side == "BUY"
    assert intent.limit_price == Decimal("350010")
    assert intent.time_in_force.value == "IOC"
    assert intent.reduce_only is False
    assert intent.stage is ReleaseStage.PAPER
    assert intent.quantity * intent.limit_price * Decimal("0.02") <= Decimal("25")


def test_equity_above_reference_capital_does_not_raise_size() -> None:
    assert candidate(equity=Decimal("50000")).intent.quantity == Decimal("0.00285")
    assert candidate(equity=Decimal("5000")).intent.quantity == Decimal("0.00142")


def test_a_held_position_uses_part_of_the_per_position_budget() -> None:
    """The Risk Engine checks the 0.25% budget per POSITION: a buy only fills what is left."""

    # Full budget: 10000 * 0.25% = 25 BRL risk / (350010 * 2%) = 0.003571 BTC of position.
    full = candidate(decision=long_decision(1.0)).intent
    assert full is not None and full.quantity == Decimal("0.00357")
    topped = candidate(decision=long_decision(1.0), position_quantity=Decimal("0.003")).intent
    assert topped is not None and topped.quantity == Decimal("0.00057")
    # Held + new, at the limit price, stays inside the budget the Risk Engine measures.
    held_and_new = (Decimal("0.003") + topped.quantity) * topped.limit_price
    assert held_and_new * Decimal("0.02") <= Decimal("25")
    # A lower conviction still sizes below the room left.
    assert candidate(position_quantity=Decimal("0.001")).intent.quantity == Decimal("0.00257")
    full_already = candidate(decision=long_decision(1.0), position_quantity=Decimal("0.0036"))
    assert full_already.intent is None
    assert full_already.abstain_reason == "risk per position limit reached"


def test_gross_leverage_headroom_caps_and_blocks_buys() -> None:
    full = candidate(position_quantity=Decimal("0.03"))  # 0.03 * 350010 > 10000
    assert full.intent is None
    assert full.abstain_reason == "gross leverage limit reached"
    near = candidate(
        position_quantity=Decimal("0.0285"),
        decision=long_decision(1.0),
        policy=SizingPolicy(stop_distance=Decimal("0.001")),
    )
    assert near.intent is not None
    assert (near.intent.quantity + Decimal("0.0285")) * Decimal("350010") <= Decimal("10000")


def test_short_sells_holdings_reduce_only_and_never_opens_a_short() -> None:
    short = fuse([signal(SignalDirection.SHORT)], instrument_id=BTC_ID, now=NOW)
    none_held = candidate(decision=short)
    assert none_held.intent is None
    assert none_held.abstain_reason == "spot venue cannot open a short"
    exit_ = candidate(decision=short, position_quantity=Decimal("0.0123456"))
    assert exit_.intent.side == "SELL"
    assert exit_.intent.reduce_only is True
    assert exit_.intent.quantity == Decimal("0.01234")
    assert exit_.intent.limit_price == Decimal("349990")


@pytest.mark.parametrize(
    ("quote", "reason"),
    [
        (None, "no market data"),
        (
            QUOTE.model_copy(update={"observed_at": NOW - timedelta(seconds=11)}),
            "stale market data",
        ),
        (QUOTE.model_copy(update={"bid": Decimal("350020")}), "crossed or invalid quote"),
    ],
)
def test_bad_market_data_abstains(quote, reason) -> None:
    assert candidate(quote=quote).abstain_reason == reason


def test_orders_below_the_venue_minimum_abstain() -> None:
    result = candidate(equity=Decimal("10"))
    assert result.intent is None
    assert result.abstain_reason in {"quantity below venue minimum", "notional below venue minimum"}


# --- review contract ------------------------------------------------------------


def review(verdict: Verdict, factor: float, **extra) -> AgentReview:
    return AgentReview(verdict=verdict, size_factor=factor, rationale="r", reviewer="t", **extra)


@pytest.mark.parametrize(
    ("verdict", "factor"),
    [(Verdict.APPROVE, 0.5), (Verdict.REDUCE, 1.0), (Verdict.REDUCE, 0.0), (Verdict.VETO, 0.2)],
)
def test_review_contract_rejects_inconsistent_factors(verdict, factor) -> None:
    with pytest.raises(ValidationError):
        review(verdict, factor)


def test_apply_review_can_only_shrink() -> None:
    intent = candidate().intent
    assert apply_review(intent, review(Verdict.APPROVE, 1.0), BTC) == intent
    reduced = apply_review(intent, review(Verdict.REDUCE, 0.5), BTC)
    assert reduced.quantity == Decimal("0.00142")
    assert reduced.intent_id == intent.intent_id
    assert reduced.fingerprint() != intent.fingerprint()
    assert apply_review(intent, review(Verdict.REDUCE, 0.001), BTC) is None
    assert apply_review(intent, review(Verdict.DEFER, 0.0), BTC) is None
    assert apply_review(intent, review(Verdict.VETO, 0.0), BTC) is None


@pytest.mark.parametrize("factor", [0.01, 0.25, 0.5, 0.75, 0.9, 0.99, 0.999999])
@pytest.mark.parametrize("equity", ["2000", "10000"])
def test_a_reduce_never_grows_and_stays_on_the_lot_grid(factor, equity) -> None:
    intent = candidate(equity=Decimal(equity)).intent
    reduced = apply_review(intent, review(Verdict.REDUCE, factor), BTC)
    if reduced is not None:
        assert reduced.quantity < intent.quantity
        assert reduced.quantity <= intent.quantity * Decimal(str(factor))
        assert reduced.quantity % BTC.lot_size == 0
        assert reduced.limit_price == intent.limit_price and reduced.side == intent.side


def test_a_forged_review_that_skipped_validation_still_cannot_grow() -> None:
    """``model_construct`` bypasses the contract; apply_review must still refuse to enlarge."""

    intent = candidate().intent
    for factor in (1.0, 1.5, 10.0):
        forged = AgentReview.model_construct(
            verdict=Verdict.REDUCE, size_factor=factor, rationale="r", reviewer="forged"
        )
        assert apply_review(intent, forged, BTC) is None


# --- Claude reviewer (fake client, no network) ----------------------------------


class FakeMessages:
    def __init__(self, behaviour) -> None:
        self.behaviour = behaviour
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return await self.behaviour()


def fake_client(behaviour):
    messages = FakeMessages(behaviour)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def reply(payload: object, *, stop_reason: str = "end_turn"):
    text = payload if isinstance(payload, str) else json.dumps(payload)

    async def behaviour():
        return SimpleNamespace(
            stop_reason=stop_reason,
            model="claude-opus-5-5",
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text=text),
            ],
        )

    return behaviour


def context() -> ReviewContext:
    return ReviewContext(
        candidate=candidate().intent,
        fusion=long_decision().to_dict(),
        market={"bid": "349990", "ask": "350010"},
        portfolio={"equity": "10000", "position_quantity": "0"},
        signals=[],
        constraints={"spot_only": True},
    )


async def test_claude_reviewer_requests_structured_output_with_fallbacks() -> None:
    client, messages = fake_client(
        reply({"verdict": "REDUCE", "size_factor": 0.5, "rationale": "wide spread"})
    )
    result = await ClaudeReviewer(client, timeout_seconds=3).review(context())
    assert result.verdict is Verdict.REDUCE
    assert result.size_factor == 0.5
    assert result.model == "claude-opus-5-5"
    assert result.prompt_sha256 and len(result.prompt_sha256) == 64
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": REVIEW_SCHEMA}
    assert call["output_config"]["effort"] == "low"
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["timeout"] == 3
    prompt = call["messages"][0]["content"]
    assert "candidate_intent" in prompt
    for forbidden in ("api_key", "secret", "password", "ANTHROPIC"):
        assert forbidden not in prompt


@pytest.mark.parametrize(
    ("behaviour", "reason"),
    [
        (reply({"verdict": "APPROVE"}, stop_reason="refusal"), "reviewer stopped: refusal"),
        (reply({"verdict": "APPROVE"}, stop_reason="max_tokens"), "reviewer stopped: max_tokens"),
        (reply("not json"), "reviewer output violated the review contract"),
        (
            reply({"verdict": "APPROVE", "size_factor": 0.4, "rationale": "x"}),
            "reviewer output violated the review contract",
        ),
        (
            reply({"verdict": "BUY_MORE", "size_factor": 2, "rationale": "x"}),
            "reviewer output violated the review contract",
        ),
    ],
)
async def test_claude_reviewer_defers_on_any_bad_answer(behaviour, reason) -> None:
    client, _ = fake_client(behaviour)
    result = await ClaudeReviewer(client).review(context())
    assert result.verdict is Verdict.DEFER
    assert result.size_factor == 0.0
    assert result.rationale == reason


async def test_claude_reviewer_defers_on_errors_and_timeouts() -> None:
    async def boom():
        raise ConnectionError("network down")

    async def slow():
        await asyncio.sleep(5)

    client, _ = fake_client(boom)
    assert (await ClaudeReviewer(client).review(context())).rationale == (
        "reviewer unavailable: ConnectionError"
    )
    client, _ = fake_client(slow)
    timed_out = await ClaudeReviewer(client, timeout_seconds=0.01).review(context())
    assert timed_out.verdict is Verdict.DEFER
    assert timed_out.rationale == "reviewer timed out"


# --- pipeline -----------------------------------------------------------------


class FixedReviewer:
    def __init__(self, verdict: Verdict, factor: float) -> None:
        self.name = f"fixed-{verdict.value.lower()}"
        self._review = review(verdict, factor)
        self.contexts: list[ReviewContext] = []

    async def review(self, ctx: ReviewContext) -> AgentReview:
        self.contexts.append(ctx)
        return self._review


def pipeline(reviewer, recorded=None, **overrides):
    async def record(decision):
        recorded.append(decision)

    values = {
        "instruments": {BTC_ID: BTC},
        "reviewer": reviewer,
        "quotes": lambda _id: QUOTE,
        "portfolio": lambda _id: PortfolioView(Decimal("10000"), Decimal("0")),
        "recorder": record if recorded is not None else None,
        "clock": lambda: NOW,
    }
    values.update(overrides)
    return HybridDecisionPipeline(**values)


async def test_deterministic_pipeline_proposes_the_candidate_and_records_it() -> None:
    recorded: list = []
    decision = await pipeline(DeterministicReviewer(), recorded).evaluate(BTC_ID, [signal()])
    assert decision.outcome is DecisionOutcome.APPROVED
    assert decision.proposal == decision.candidate
    assert recorded == [decision]
    payload = decision.to_dict()
    assert payload["order_authority"] is False
    assert payload["proposal_fingerprint"] == decision.proposal.fingerprint()
    json.dumps(payload)


@pytest.mark.parametrize(
    ("verdict", "factor", "outcome", "has_proposal"),
    [
        (Verdict.REDUCE, 0.5, DecisionOutcome.REDUCED, True),
        (Verdict.REDUCE, 0.001, DecisionOutcome.ABSTAINED, False),
        (Verdict.DEFER, 0.0, DecisionOutcome.DEFERRED, False),
        (Verdict.VETO, 0.0, DecisionOutcome.VETOED, False),
    ],
)
async def test_review_verdicts_map_to_outcomes(verdict, factor, outcome, has_proposal) -> None:
    decision = await pipeline(FixedReviewer(verdict, factor)).evaluate(BTC_ID, [signal()])
    assert decision.outcome is outcome
    assert (decision.proposal is not None) is has_proposal


async def test_reviewer_is_not_consulted_without_a_candidate() -> None:
    reviewer = FixedReviewer(Verdict.APPROVE, 1.0)
    no_trade = await pipeline(reviewer).evaluate(BTC_ID, [])
    no_data = await pipeline(reviewer, quotes=lambda _id: None).evaluate(BTC_ID, [signal()])
    unknown = await pipeline(reviewer).evaluate("BINANCE_SPOT:DOGEBRL", [signal()])
    assert no_trade.outcome is DecisionOutcome.NO_TRADE
    assert no_data.outcome is DecisionOutcome.ABSTAINED
    assert no_data.abstain_reason == "no market data"
    assert unknown.abstain_reason == "unknown instrument"
    assert reviewer.contexts == []


async def test_reviewer_context_has_market_portfolio_and_constraints_only() -> None:
    reviewer = FixedReviewer(Verdict.APPROVE, 1.0)
    supporting = signal()
    await pipeline(reviewer).evaluate(BTC_ID, [supporting, signal(instrument_id="X:Y")])
    ctx = reviewer.contexts[0]
    assert ctx.market["spread_bps"] == "0.57"
    assert ctx.constraints["max_risk_per_position"] == "0.0025"
    assert [s["signal_id"] for s in ctx.signals] == [str(supporting.signal_id)]


def test_pipeline_refuses_real_money_stages() -> None:
    for stage in (ReleaseStage.CANARY, ReleaseStage.LIVE):
        with pytest.raises(ValueError, match="real-money"):
            pipeline(DeterministicReviewer(), stage=stage)


# --- offline promotion gate -----------------------------------------------------


class OracleReviewer:
    """Vetoes the cases the test marks as losers (or winners when inverted)."""

    def __init__(self, losers: set, *, invert: bool = False) -> None:
        self.name = "oracle"
        self._losers = losers
        self._invert = invert

    async def review(self, ctx: ReviewContext) -> AgentReview:
        loser = ctx.candidate.intent_id in self._losers
        if loser != self._invert:
            return review(Verdict.VETO, 0.0)
        return review(Verdict.APPROVE, 1.0)


def replay_cases(n: int = 60):
    cases, losers = [], set()
    for i in range(n):
        ctx = context()
        ctx = ReviewContext(
            candidate=candidate().intent,
            fusion=ctx.fusion,
            market=ctx.market,
            portfolio=ctx.portfolio,
            signals=[],
            constraints=ctx.constraints,
        )
        realized = Decimal("-0.01") if i % 3 == 0 else Decimal("0.005")
        if realized < 0:
            losers.add(ctx.candidate.intent_id)
        cases.append(ReplayCase(ctx, realized))
    return cases, losers


async def test_reviewer_that_avoids_losers_is_promotable() -> None:
    cases, losers = replay_cases()
    result = await evaluate_reviewer(cases, OracleReviewer(losers), {BTC_ID: BTC})
    assert result.promotable is True
    assert result.reviewed_pnl > result.baseline_pnl
    assert result.reviewed_max_drawdown <= result.baseline_max_drawdown
    assert result.verdicts["VETO"] == 20


async def test_reviewer_that_avoids_winners_is_not_promotable() -> None:
    cases, losers = replay_cases()
    result = await evaluate_reviewer(cases, OracleReviewer(losers, invert=True), {BTC_ID: BTC})
    assert result.promotable is False
    assert result.reason == "reviewed PnL below the deterministic baseline"


async def test_a_sell_candidate_counts_its_own_signed_return() -> None:
    """``realized_return`` is already the trade's return: a SELL that avoided a fall is a gain."""

    exit_decision = fuse(
        [signal(SignalDirection.SHORT, strength=0.9)], instrument_id=BTC_ID, now=NOW
    )
    sell = candidate(decision=exit_decision, position_quantity=Decimal("0.01")).intent
    assert sell is not None and sell.side == "SELL"
    ctx = context()
    case = ReplayCase(
        ReviewContext(
            candidate=sell,
            fusion=ctx.fusion,
            market=ctx.market,
            portfolio=ctx.portfolio,
            signals=[],
            constraints=ctx.constraints,
        ),
        Decimal("0.01"),
    )
    result = await evaluate_reviewer([case], DeterministicReviewer(), {BTC_ID: BTC}, min_cases=1)
    assert result.baseline_pnl == sell.quantity * sell.limit_price * Decimal("0.01") > 0
    assert result.reviewed_pnl == result.baseline_pnl


async def test_promotion_needs_enough_cases() -> None:
    cases, losers = replay_cases(10)
    result = await evaluate_reviewer(cases, OracleReviewer(losers), {BTC_ID: BTC})
    assert result.promotable is False
    assert result.reason == "needs at least 50 cases"
