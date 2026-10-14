"""Adversarial suite: hostile intents and forged permits against the real Risk Engine and permits.

Fixed attacks must each be refused for the expected reason. A seeded fuzz then throws hundreds of
random intents and accounts at the engine; every intent it approves is re-checked here against the
constitution, computed independently of the engine's own code.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.execution import OrderType, ReleaseStage, TradeIntent, VenueId
from services.execution.catalog import PAPER_INSTRUMENTS
from services.execution.constitution import (
    CANARY_CAPITAL,
    MAX_GROSS_LEVERAGE,
    MAX_RISK_PER_POSITION,
    REFERENCE_CAPITAL,
    canary_capital_in,
)
from services.execution.venue import Quote
from services.risk import (
    AccountState,
    Controls,
    Ed25519PermitSigner,
    Ed25519PermitVerifier,
    LockdownReason,
    PermitLedger,
    PermitRejected,
    PermitSigner,
    RiskEngine,
    RiskPolicy,
)

from .report import DrillReport

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
MIDS = {"BTC": Decimal("62000"), "ETH": Decimal("3100"), "SOL": Decimal("150")}
SIGNER = PermitSigner(b"drill-adversarial-hmac-key-000001")
POLICY = RiskPolicy()
FUZZ_CASES = 400


def _quote(instrument_id: str, *, age: timedelta = timedelta(0)) -> Quote:
    base = PAPER_INSTRUMENTS[instrument_id].base_asset
    mid = MIDS[base] * (Decimal("5.2") if instrument_id.endswith("BRL") else Decimal("1"))
    spread = mid * Decimal("0.00001")
    return Quote(bid=mid - spread, ask=mid + spread, observed_at=NOW - age)


def _account(**overrides) -> AccountState:
    values = {
        "equity": Decimal("10000"),
        "start_of_day_equity": Decimal("10000"),
        "peak_equity": Decimal("10000"),
        "gross_exposure": Decimal("0"),
        "positions": {},
    }
    values.update(overrides)
    return AccountState(**values)


def _intent(n: int, **overrides) -> TradeIntent:
    values = {
        "intent_id": UUID(int=n),
        "instrument_id": "BINANCE_SPOT:BTCUSDT",
        "venue": VenueId.BINANCE_SPOT,
        "side": "BUY",
        "quantity": Decimal("0.002"),
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal("62000.62"),
        "strategy_id": "drill-adversarial",
        "model_version": "drill",
        "signal_ids": (UUID(int=10_000 + n),),
        "created_at": NOW,
    }
    values.update(overrides)
    return TradeIntent.model_validate(values)


def _refusal(decision) -> str:
    return "approved" if decision.approved else ",".join(sorted(decision.rejections))


def _fixed_attacks(report: DrillReport, engine: RiskEngine) -> None:
    btc = "BINANCE_SPOT:BTCUSDT"
    calm, quote = Controls(), _quote(btc)

    def judge(name: str, expected_reason: str, intent: TradeIntent, **kw) -> None:
        decision = engine.evaluate(
            intent,
            account=kw.get("account", _account()),
            controls=kw.get("controls", calm),
            quote=kw.get("quote", quote),
            now=NOW,
            scope_stage=kw.get("scope_stage"),
            canary_capital=kw.get("canary_capital"),
        )
        refused = not decision.approved and decision.permit is None
        hit = expected_reason in decision.rejections
        report.expect(
            name,
            f"refused:{expected_reason}",
            f"refused:{expected_reason}" if refused and hit else _refusal(decision),
        )

    report.expect(
        "baseline_compliant_intent",
        "approved",
        _refusal(
            engine.evaluate(_intent(1), account=_account(), controls=calm, quote=quote, now=NOW)
        ),
    )
    judge("oversized_position", "risk_per_position", _intent(2, quantity=Decimal("0.3")))
    judge("price_far_above_market", "price_collar", _intent(3, limit_price=Decimal("62400.00")))
    judge("short_sale", "spot_no_short", _intent(4, side="SELL", reduce_only=True))
    judge(
        "sell_without_reduce_only",
        "sell_is_reduce_only",
        _intent(5, side="SELL"),
        account=_account(positions={btc: Decimal("1")}),
    )
    judge(
        "unknown_instrument", "instrument_known", _intent(6, instrument_id="BINANCE_SPOT:DOGEUSDT")
    )
    judge("off_grid_quantity", "venue_grid", _intent(7, quantity=Decimal("0.0000150")))
    judge(
        "uncertified_canary",
        "stage_certified",
        _intent(8, stage=ReleaseStage.CANARY),
        scope_stage=ReleaseStage.PAPER,
    )
    judge(
        "live_stage",
        "stage_certified",
        _intent(9, stage=ReleaseStage.LIVE),
        scope_stage=ReleaseStage.LIVE,
    )
    judge("stale_quote", "fresh_quote", _intent(10), quote=_quote(btc, age=timedelta(minutes=1)))
    judge("missing_quote", "fresh_quote", _intent(11), quote=None)
    judge(
        "global_kill_switch", "global_kill_switch", _intent(12), controls=Controls(global_kill=True)
    )
    judge(
        "venue_kill_switch",
        "venue_kill_switch",
        _intent(13),
        controls=Controls(killed_venues=frozenset({"BINANCE_SPOT"})),
    )
    judge(
        "latched_lockdown",
        "no_lockdown",
        _intent(14),
        controls=Controls(lockdown=LockdownReason.DAILY_LOSS),
    )
    judge("daily_loss_breach", "no_lockdown", _intent(15), account=_account(equity=Decimal("9899")))
    judge(
        "drawdown_breach",
        "no_lockdown",
        _intent(16),
        account=_account(equity=Decimal("9499"), start_of_day_equity=Decimal("9500")),
    )
    judge(
        "leverage_over_one",
        "gross_leverage",
        _intent(17),
        account=_account(gross_exposure=Decimal("9950")),
    )
    judge(
        "canary_sized_on_canary_capital",
        "risk_per_position",
        _intent(18, quantity=Decimal("0.002"), stage=ReleaseStage.CANARY),
        scope_stage=ReleaseStage.CANARY,
        canary_capital=CANARY_CAPITAL,
    )
    judge(
        "canary_without_brl_rate",
        "canary_capital_known",
        _intent(19, quantity=Decimal("0.0001"), stage=ReleaseStage.CANARY),
        scope_stage=ReleaseStage.CANARY,
    )
    # R$500 at 5.50 BRL/USDT is about 90.9 USDT, a 0.227 risk budget. A 12.4 USDT order risks
    # 0.248: refused, where reading R$500 as 500 USDT (budget 1.25) would have approved it.
    judge(
        "canary_capped_in_brl_not_usdt",
        "risk_per_position",
        _intent(20, quantity=Decimal("0.0002"), stage=ReleaseStage.CANARY),
        scope_stage=ReleaseStage.CANARY,
        canary_capital=canary_capital_in(Decimal("5.50")),
    )


def _permit_attacks(report: DrillReport) -> None:
    trade, other = _intent(100), _intent(101, quantity=Decimal("0.003"))

    def outcome(verify) -> str:
        try:
            verify()
        except PermitRejected as exc:
            return f"rejected:{exc.code}"
        return "accepted"

    ledger = PermitLedger(SIGNER)
    permit = SIGNER.issue(trade, now=NOW)
    report.expect(
        "genuine_permit", "accepted", outcome(lambda: ledger.consume(permit, trade, now=NOW))
    )
    report.expect(
        "replayed_permit",
        "rejected:already_used",
        outcome(lambda: ledger.consume(permit, trade, now=NOW)),
    )
    forged = PermitSigner(b"attacker-key-attacker-key-000001").issue(trade, now=NOW)
    report.expect(
        "forged_signature",
        "rejected:bad_signature",
        outcome(lambda: PermitLedger(SIGNER).verify(forged, trade, now=NOW)),
    )
    fresh = SIGNER.issue(trade, now=NOW)
    report.expect(
        "permit_for_another_intent",
        "rejected:intent_mismatch",
        outcome(lambda: PermitLedger(SIGNER).verify(fresh, other, now=NOW)),
    )
    tampered = fresh.model_copy(update={"max_quantity": Decimal("1")})
    report.expect(
        "tampered_quantity",
        "rejected:bad_signature",
        outcome(lambda: PermitLedger(SIGNER).verify(tampered, trade, now=NOW)),
    )
    late = NOW + timedelta(minutes=5)
    report.expect(
        "expired_permit",
        "rejected:expired",
        outcome(lambda: PermitLedger(SIGNER).verify(fresh, trade, now=late)),
    )
    seed = bytes(range(32))
    ed = Ed25519PermitSigner(seed)
    verifier = Ed25519PermitVerifier(
        Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    )
    report.expect(
        "ed25519_genuine",
        "accepted",
        outcome(lambda: verifier.verify(ed.issue(trade, now=NOW), trade, now=NOW)),
    )
    report.expect(
        "hmac_permit_to_ed25519_executor",
        "rejected:bad_signature",
        outcome(lambda: verifier.verify(fresh, trade, now=NOW)),
    )


def _fuzz(report: DrillReport, engine: RiskEngine, seed: int) -> None:
    rng = random.Random(seed)
    ids = sorted(PAPER_INSTRUMENTS)
    approved = violations = 0
    first_violation = ""
    for n in range(FUZZ_CASES):
        instrument_id = rng.choice(ids)
        instrument = PAPER_INSTRUMENTS[instrument_id]
        quote = _quote(instrument_id, age=timedelta(seconds=rng.choice([0, 1, 5, 9, 11, 30])))
        side = "BUY" if rng.random() < 0.85 else "SELL"
        touch = quote.ask if side == "BUY" else quote.bid
        price = (touch * (1 + Decimal(rng.randint(-120, 120)) / Decimal(10_000))).quantize(
            instrument.tick_size
        )
        quantity = (
            Decimal(10) ** Decimal(rng.uniform(-5, 0.5)) * Decimal(rng.randint(1, 9))
        ).quantize(instrument.lot_size)
        equity = Decimal(rng.randint(8_000, 12_000))
        account = _account(
            equity=equity,
            start_of_day_equity=equity * (1 + Decimal(rng.randint(0, 150)) / Decimal(10_000)),
            peak_equity=equity * (1 + Decimal(rng.randint(0, 700)) / Decimal(10_000)),
            gross_exposure=Decimal(rng.randint(0, 11_000)),
            positions={instrument_id: Decimal(rng.choice(["0", "0", "0.01", "1"]))},
        )
        controls = Controls(
            global_kill=rng.random() < 0.08,
            lockdown=LockdownReason.DRAWDOWN if rng.random() < 0.05 else None,
        )
        intent = _intent(
            1_000 + n,
            instrument_id=instrument_id,
            side=side,
            quantity=max(quantity, instrument.lot_size),
            limit_price=price,
            reduce_only=side == "SELL" and rng.random() < 0.7,
        )
        decision = engine.evaluate(intent, account=account, controls=controls, quote=quote, now=NOW)
        if not decision.approved:
            continue
        approved += 1
        problems = _constitution_violations(intent, account, controls, quote, decision)
        if problems:
            violations += 1
            first_violation = first_violation or f"{n}:{problems}"
    report.expect("fuzz_cases", FUZZ_CASES, FUZZ_CASES)
    report.expect("fuzz_approved_some", True, approved > 0)
    report.expect(
        "fuzz_constitution_violations",
        0,
        f"{violations}{' ' + first_violation if first_violation else ''}",
    )
    report.expect(
        "fuzz_approved_count", approved, approved
    )  # pins the seed's outcome into the digest


def _constitution_violations(intent, account, controls, quote, decision) -> list[str]:
    """Independent re-check of an approved intent against the constitution."""

    problems: list[str] = []
    if controls.global_kill or controls.lockdown is not None:
        problems.append("approved_under_halt")
    if decision.permit is None or decision.permit.max_quantity != intent.quantity:
        problems.append("permit_mismatch")
    if NOW - quote.observed_at > POLICY.max_quote_age:
        problems.append("stale_quote")
    reducing = (
        intent.side == "SELL"
        and intent.reduce_only
        and intent.quantity <= account.positions.get(intent.instrument_id, Decimal("0"))
    )
    if intent.side == "SELL" and not reducing:
        problems.append("unbacked_sell")
    if not reducing:
        equity = min(account.equity, REFERENCE_CAPITAL) if account.equity > 0 else Decimal("0")
        notional = intent.quantity * intent.limit_price
        if notional * POLICY.stop_distance > equity * MAX_RISK_PER_POSITION:
            problems.append("risk_per_position")
        if account.gross_exposure + notional > equity * MAX_GROSS_LEVERAGE:
            problems.append("gross_leverage")
        if (account.start_of_day_equity - account.equity) / account.start_of_day_equity >= Decimal(
            "0.01"
        ):
            problems.append("daily_loss")
        if (account.peak_equity - account.equity) / account.peak_equity >= Decimal("0.05"):
            problems.append("drawdown")
    return problems


def run(seed: int = 0, *, engine: RiskEngine | None = None) -> DrillReport:
    """`engine` lets a test prove the drill catches a broken engine; otherwise the real one."""

    engine = engine or RiskEngine(instruments=PAPER_INSTRUMENTS, signer=SIGNER, policy=POLICY)
    report = DrillReport(kind="ADVERSARIAL_SUITE", seed=seed)
    for name, section in (
        ("fixed_attacks", lambda: _fixed_attacks(report, engine)),
        ("permit_attacks", lambda: _permit_attacks(report)),
        ("fuzz", lambda: _fuzz(report, engine, seed)),
    ):
        try:
            section()
        except Exception as exc:  # recorded as a failed case
            report.guard(name, exc)
    return report
