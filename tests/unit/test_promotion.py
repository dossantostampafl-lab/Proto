"""Phase 6: promotion gates, automatic demotion, Ed25519 permits."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from services.execution import OrderType, ReleaseStage, TradeIntent, VenueId
from services.promotion import EvidenceKind, PromotionRefused, PromotionRegistry, Scope
from services.promotion.registry import promotion_table
from services.risk import (
    Ed25519PermitSigner,
    Ed25519PermitVerifier,
    PermitLedger,
    PermitRejected,
    PermitSigner,
)
from services.risk.permit_keys import decode_key, generate

START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
SCOPE = Scope("executor-binance-spot", "BINANCE_SPOT", "hybrid-fusion-v1", "fusion-v1", "testnet")
OTHER_VENUE = Scope("paper-xp", "XP_B3", "hybrid-fusion-v1", "fusion-v1", "paper")
CANARY_EVIDENCE = (
    EvidenceKind.ADVERSARIAL_SUITE,
    EvidenceKind.RECOVERY_DRILL,
    EvidenceKind.ROLLBACK_DRILL,
    EvidenceKind.WATCHDOG_ACTIVE,
)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


async def to_paper(registry: PromotionRegistry, scope: Scope = SCOPE) -> None:
    await registry.promote(scope, ReleaseStage.REPLAY, operator=False, by="test")
    await registry.add_evidence(
        scope, EvidenceKind.REPLAY_REPRODUCIBLE, reference="ci#1", passed=True, by="t"
    )
    await registry.promote(scope, ReleaseStage.SHADOW, operator=False, by="test")
    await registry.add_evidence(
        scope, EvidenceKind.SHADOW_COMPARISON, reference="shadow#1", passed=True, by="t"
    )
    await registry.promote(scope, ReleaseStage.PAPER, operator=False, by="test")


async def canary_evidence(registry: PromotionRegistry, scope: Scope = SCOPE) -> None:
    for kind in CANARY_EVIDENCE:
        await registry.add_evidence(
            scope, kind, reference=f"{kind.value.lower()}#ok", passed=True, by="t"
        )


async def test_stages_advance_one_at_a_time_with_evidence() -> None:
    clock = Clock()
    registry = PromotionRegistry(clock=clock)
    with pytest.raises(PromotionRefused, match="next stage"):
        await registry.promote(SCOPE, ReleaseStage.PAPER, operator=True, by="t")
    await registry.promote(SCOPE, ReleaseStage.REPLAY, operator=False, by="t")
    with pytest.raises(PromotionRefused, match="missing REPLAY_REPRODUCIBLE"):
        await registry.promote(SCOPE, ReleaseStage.SHADOW, operator=False, by="t")
    await registry.add_evidence(
        SCOPE, EvidenceKind.REPLAY_REPRODUCIBLE, reference="ci#9", passed=False, by="t"
    )
    with pytest.raises(PromotionRefused, match="latest REPLAY_REPRODUCIBLE evidence failed"):
        await registry.promote(SCOPE, ReleaseStage.SHADOW, operator=False, by="t")
    await registry.add_evidence(
        SCOPE, EvidenceKind.REPLAY_REPRODUCIBLE, reference="ci#10", passed=True, by="t"
    )
    record = await registry.promote(SCOPE, ReleaseStage.SHADOW, operator=False, by="t")
    assert record.stage is ReleaseStage.SHADOW
    assert [h["to"] for h in record.history] == ["REPLAY", "SHADOW"]


async def test_canary_needs_operator_a_clean_week_and_fresh_evidence() -> None:
    clock = Clock()
    registry = PromotionRegistry(clock=clock)
    await canary_evidence(registry)  # gathered before PAPER: does not count
    clock.advance(minutes=1)
    await to_paper(registry)
    clock.advance(days=3)
    with pytest.raises(PromotionRefused) as refused:
        await registry.promote(SCOPE, ReleaseStage.CANARY, operator=False, by="t")
    reasons = " ".join(refused.value.reasons)
    assert "needs an operator" in reasons
    assert "needs 7 clean days in PAPER (has 3.0)" in reasons
    assert "missing WATCHDOG_ACTIVE" in reasons

    await canary_evidence(registry)
    clock.advance(days=4)
    record = await registry.promote(SCOPE, ReleaseStage.CANARY, operator=True, by="t")
    assert record.stage is ReleaseStage.CANARY
    with pytest.raises(PromotionRefused, match="LIVE certification is not available"):
        await registry.promote(SCOPE, ReleaseStage.LIVE, operator=True, by="t")


async def test_stale_evidence_does_not_count() -> None:
    clock = Clock()
    registry = PromotionRegistry(clock=clock)
    await to_paper(registry)
    await canary_evidence(registry)
    clock.advance(days=15)
    with pytest.raises(PromotionRefused, match="missing ADVERSARIAL_SUITE"):
        await registry.promote(SCOPE, ReleaseStage.CANARY, operator=True, by="t")


async def test_violation_demotes_canary_and_restarts_the_clean_week() -> None:
    clock = Clock()
    registry = PromotionRegistry(clock=clock)
    await to_paper(registry)
    await to_paper(registry, OTHER_VENUE)
    clock.advance(days=7)
    await canary_evidence(registry)
    await registry.promote(SCOPE, ReleaseStage.CANARY, operator=True, by="t")

    clock.advance(hours=2)
    touched = await registry.violation(
        venue="BINANCE_SPOT", kind="RECONCILIATION_DIVERGENCE", detail="x"
    )
    assert [r.scope for r in touched] == [SCOPE]
    assert await registry.stage(SCOPE) is ReleaseStage.PAPER
    assert await registry.stage(OTHER_VENUE) is ReleaseStage.PAPER  # other venue untouched
    assert (await registry.get(OTHER_VENUE)).violations == []

    await canary_evidence(registry)
    clock.advance(days=6)
    with pytest.raises(PromotionRefused, match="needs 7 clean days"):
        await registry.promote(SCOPE, ReleaseStage.CANARY, operator=True, by="t")
    clock.advance(days=1)
    await canary_evidence(registry)
    assert (
        await registry.promote(SCOPE, ReleaseStage.CANARY, operator=True, by="t")
    ).stage is ReleaseStage.CANARY


async def test_registry_survives_a_restart(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'p.db'}")
    clock = Clock()
    first = PromotionRegistry(engine, clock=clock)
    await to_paper(first)
    second = PromotionRegistry(engine, clock=clock)
    record = await second.get(SCOPE)
    assert record.stage is ReleaseStage.PAPER
    assert {e.kind for e in record.evidence} == {
        EvidenceKind.REPLAY_REPRODUCIBLE,
        EvidenceKind.SHADOW_COMPARISON,
    }
    await engine.dispose()


def test_promotion_migration_matches_the_runtime_table(monkeypatch) -> None:
    path = Path("migrations/versions/0010_promotion_scopes.py")
    spec = importlib.util.spec_from_file_location("migration_0010", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0009_oms_state"
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        columns = {c["name"] for c in inspect(connection).get_columns(migration.TABLE_NAME)}
        assert columns == set(promotion_table.columns.keys())
        migration.downgrade()


# --- Ed25519 permits -----------------------------------------------------------------


def trade() -> TradeIntent:
    return TradeIntent.model_validate(
        {
            "intent_id": uuid4(),
            "instrument_id": "BINANCE_SPOT:BTCUSDT",
            "venue": VenueId.BINANCE_SPOT,
            "side": "BUY",
            "quantity": Decimal("0.001"),
            "order_type": OrderType.LIMIT,
            "limit_price": Decimal("62000"),
            "strategy_id": "s",
            "model_version": "m",
            "signal_ids": (uuid4(),),
            "created_at": START,
        }
    )


def test_executor_verifies_ed25519_permits_but_cannot_mint_them() -> None:
    private_b64, public_b64 = generate()
    signer = Ed25519PermitSigner(decode_key(private_b64))
    verifier = Ed25519PermitVerifier(decode_key(public_b64))
    intent = trade()
    permit = signer.issue(intent, now=START)
    assert permit.signature.startswith("ed25519:")
    verifier.verify(permit, intent, now=START)

    ledger = PermitLedger(verifier)
    ledger.consume(permit, intent, now=START)
    with pytest.raises(PermitRejected, match="already_used"):
        ledger.consume(permit, intent, now=START)

    hmac_permit = PermitSigner(b"k" * 32).issue(intent, now=START)
    with pytest.raises(PermitRejected, match="bad_signature"):
        verifier.verify(hmac_permit, intent, now=START)
    forged = Ed25519PermitSigner(decode_key(generate()[0])).issue(intent, now=START)
    with pytest.raises(PermitRejected, match="bad_signature"):
        verifier.verify(forged, intent, now=START)
    tampered = permit.model_copy(update={"max_quantity": Decimal("5")})
    with pytest.raises(PermitRejected, match="bad_signature"):
        verifier.verify(tampered, intent, now=START)
    with pytest.raises(PermitRejected, match="expired"):
        verifier.verify(permit, intent, now=START + timedelta(seconds=11))
    with pytest.raises(ValueError):
        decode_key("bm90LWEta2V5")


# --- API surface -------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch):
    from apps.api.app import main, oms_surface, promotion_surface, risk_surface
    from apps.api.app.settings import Settings

    configured = Settings(operator_api_token="o" * 32)
    for module in (main, oms_surface, promotion_surface, risk_surface):
        monkeypatch.setattr(module, "settings", configured)
    for reset in (
        promotion_surface.reset_promotion_state,
        risk_surface.reset_risk_state,
        oms_surface.reset_oms_state,
    ):
        reset()
    yield promotion_surface
    for reset in (
        promotion_surface.reset_promotion_state,
        risk_surface.reset_risk_state,
        oms_surface.reset_oms_state,
    ):
        reset()


def test_operator_drives_promotion_through_the_api(api) -> None:
    from fastapi.testclient import TestClient

    from apps.api.app import main

    headers = {"X-Proto-Operator-Token": "o" * 32}
    scope = {
        "account": "paper-binance-spot",
        "venue": "BINANCE_SPOT",
        "strategy_id": "hybrid-fusion-v1",
        "model_version": "fusion-v1",
        "credential_id": "paper",
    }
    with TestClient(main.app) as client:
        assert client.get("/v1/promotion/policy").json()["canary_capital_brl"] == "500"
        assert (
            client.post(
                "/v1/promotion/promote", json={"scope": scope, "target": "REPLAY"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/v1/promotion/promote", json={"scope": scope, "target": "REPLAY"}, headers=headers
            ).json()["scope"]["stage"]
            == "REPLAY"
        )
        refused = client.post(
            "/v1/promotion/promote", json={"scope": scope, "target": "SHADOW"}, headers=headers
        )
        assert refused.status_code == 409
        assert "missing REPLAY_REPRODUCIBLE" in refused.json()["detail"]["refused"][0]
        client.post(
            "/v1/promotion/evidence",
            headers=headers,
            json={
                "scope": scope,
                "kind": "REPLAY_REPRODUCIBLE",
                "reference": "ci#42",
                "passed": True,
            },
        )
        moved = client.post(
            "/v1/promotion/promote", json={"scope": scope, "target": "SHADOW"}, headers=headers
        )
        assert moved.json()["scope"]["stage"] == "SHADOW"
        bad = dict(scope, account="a|b")
        assert (
            client.post(
                "/v1/promotion/evidence",
                headers=headers,
                json={
                    "scope": bad,
                    "kind": "REPLAY_REPRODUCIBLE",
                    "reference": "ci#1",
                    "passed": True,
                },
            ).status_code
            == 422
        )
        listed = client.get("/v1/promotion/scopes").json()["scopes"]
    assert [s["stage"] for s in listed] == ["SHADOW"]


async def test_divergence_demotes_a_canary_scope_through_the_oms(api) -> None:
    from apps.api.app import oms_surface
    from apps.api.app.event_state import operational_journal

    clock = Clock()
    registry = PromotionRegistry(clock=clock)
    scope = Scope("paper-binance-spot", "BINANCE_SPOT", "hybrid-fusion-v1", "fusion-v1", "paper")
    await to_paper(registry, scope)
    clock.advance(days=7)
    await canary_evidence(registry, scope)
    await registry.promote(scope, ReleaseStage.CANARY, operator=True, by="t")
    api._registry = registry

    await oms_surface._halt_venue([])
    assert await registry.stage(scope) is ReleaseStage.PAPER
    assert operational_journal.journal.recent(1)[0].event_type == "PROMOTION_VIOLATION"


async def test_testnet_executor_refuses_real_money_stages(tmp_path) -> None:
    from apps.executor.runtime import Executor
    from apps.executor.settings import ExecutorSettings

    executor = Executor(ExecutorSettings(api_token="t" * 32, permit_public_key=generate()[1]))

    class Ready:
        orders: list = []

    executor.oms = Ready()  # type: ignore[assignment]
    canary = trade().model_copy(update={"stage": ReleaseStage.CANARY})
    with pytest.raises(ValueError, match="certified live executor"):
        await executor.submit(canary, None)  # type: ignore[arg-type]
