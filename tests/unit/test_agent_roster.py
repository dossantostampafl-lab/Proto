"""Trader-agent roster: virtual R$200 agents, codes never reused, survival sizing, elimination."""

from __future__ import annotations

import importlib.util
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.app import (
    agent_surface,
    floor_surface,
    main,
    oms_surface,
    risk_surface,
    roster_surface,
    signal_surface,
)
from apps.api.app.event_state import operational_journal
from apps.api.app.settings import Settings
from services.agents.roster import (
    AGENT_STARTING_CAPITAL,
    INITIAL_SLOTS,
    AgentBook,
    AgentEliminated,
    AgentRoster,
    AgentStatus,
    AllocationRefused,
    Slot,
    SlotKind,
    TraderAgent,
    agent_orders_table,
    agents_table,
    entry_min_score,
    shape_for_agent,
    size_multiplier,
    slot_of,
)
from services.execution import (
    OrderType,
    Signal,
    SignalDirection,
    SignalSource,
    TimeInForce,
    TradeIntent,
    VenueId,
)
from services.execution.catalog import PAPER_INSTRUMENTS
from services.execution.constitution import REFERENCE_CAPITAL

BTC = "BINANCE_SPOT:BTCUSDT"
SOL = "BINANCE_SPOT:SOLUSDT"
SECRET = "w" * 32
FX = Decimal("5.5")


class Journal:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    async def __call__(self, event_type: str, payload: dict, key: str) -> None:
        self.events.append((event_type, payload, key))

    def types(self) -> list[str]:
        return [event[0] for event in self.events]


def tv_slot(instrument_id: str = BTC) -> Slot:
    return Slot(SlotKind.TRADINGVIEW, instrument_id)


def ema_slot(instrument_id: str = BTC) -> Slot:
    return Slot(SlotKind.EMA, instrument_id)


def intent(side: str, quantity: str, price: str, instrument_id: str = BTC) -> TradeIntent:
    return TradeIntent(
        intent_id=uuid4(),
        instrument_id=instrument_id,
        venue=VenueId.BINANCE_SPOT,
        side=side,
        quantity=Decimal(quantity),
        order_type=OrderType.LIMIT,
        limit_price=Decimal(price),
        time_in_force=TimeInForce.IOC,
        reduce_only=side == "SELL",
        strategy_id="hybrid-fusion-v1",
        model_version="fusion-v1",
        signal_ids=(uuid4(),),
        created_at=datetime.now(UTC),
    )


def order(intent_id: str, side: str, filled: str, average: str | None, fee: str, **extra) -> dict:
    return {
        "client_order_id": f"c-{intent_id[:8]}",
        "intent_id": intent_id,
        "instrument_id": BTC,
        "side": side,
        "status": "FILLED",
        "filled_quantity": filled,
        "average_price": average,
        "fee": fee,
        **extra,
    }


def signal(source: SignalSource, strategy_id: str, strength: float = 0.9) -> Signal:
    now = datetime.now(UTC)
    return Signal(
        signal_id=uuid4(),
        source=source,
        strategy_id=strategy_id,
        instrument_id=BTC,
        direction=SignalDirection.LONG,
        strength=strength,
        timeframe="1m",
        observed_at=now,
        received_at=now,
    )


# --- seeding and codes -------------------------------------------------------------------


async def test_seeding_gives_six_slots_one_agent_each_and_is_idempotent(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'roster.db'}")
    journal = Journal()
    try:
        roster = AgentRoster(engine, recorder=journal)
        spawned = await roster.seed()
        assert len(spawned) == 6
        assert await roster.seed() == []
        codes = [a.agent_id for a in roster.agents()]
        assert codes[0] == "AG-0001-ORION" and codes[5] == "AG-0006-RIGEL"
        assert {a.slot for a in roster.agents()} == set(INITIAL_SLOTS)
        assert all(a.capital_brl == Decimal("200") and a.generation == 0 for a in spawned)
        assert roster.allocated() == Decimal("1200")
        assert journal.types() == ["AGENT_SPAWNED"] * 6

        # A restart loads the same roster and seeds nothing.
        restarted = AgentRoster(engine, recorder=journal)
        assert await restarted.seed() == []
        assert [a.agent_id for a in restarted.agents()] == codes
        ema = restarted.active_for(ema_slot())
        assert ema is not None and ema.params == {"fast": 5, "slow": 20}
    finally:
        await engine.dispose()


async def test_codes_are_unique_and_never_reused_after_elimination(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'roster.db'}")
    try:
        roster = AgentRoster(engine)
        await roster.seed()
        first = roster.active_for(ema_slot())
        assert first is not None
        dead, replacement = await roster.eliminate(first.agent_id, reason="test")
        assert dead.status is AgentStatus.ELIMINATED and dead.eliminated_at is not None
        assert replacement is not None and replacement.agent_id == "AG-0007-SIRIUS"
        assert replacement.generation == 1
        assert replacement.params == {"fast": 8, "slow": 21}
        assert replacement.capital_brl == AGENT_STARTING_CAPITAL

        # After a restart the sequence keeps climbing; the dead code stays dead.
        restarted = AgentRoster(engine)
        await restarted.load()
        assert not restarted.admit(first.agent_id)
        assert restarted.get(first.agent_id).elimination_reason == "test"
        second = restarted.active_for(ema_slot())
        _, third = await restarted.eliminate(second.agent_id, reason="test")
        assert third.agent_id == "AG-0008-ALTAIR" and third.params == {"fast": 12, "slow": 26}
        codes = [a.agent_id for a in restarted.agents()]
        assert len(codes) == len(set(codes)) == 8
        with pytest.raises(AgentEliminated):
            await restarted.eliminate(first.agent_id, reason="again")

        # The table itself refuses a reused sequence number.
        row = {c.name: None for c in agents_table.columns}
        async with engine.connect() as connection:
            existing = (await connection.execute(agents_table.select())).mappings().first()
        row.update(existing)
        row["agent_id"] = "AG-0001-COPY"
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(agents_table.insert().values(**row))
    finally:
        await engine.dispose()


def test_signals_route_to_their_slot() -> None:
    assert slot_of(signal(SignalSource.TRADINGVIEW, "tv-ema")) == tv_slot()
    assert slot_of(signal(SignalSource.PROTO_STRATEGY, "proto-ema-cross-1m")) == ema_slot()
    assert slot_of(signal(SignalSource.PROTO_AGENT, "x")) is None
    assert slot_of(signal(SignalSource.PROTO_STRATEGY, "other")) is None


async def test_tradingview_replacements_raise_their_strength_bar() -> None:
    roster = AgentRoster()
    await roster.seed()
    agent = roster.active_for(tv_slot())
    _, replacement = await roster.eliminate(agent.agent_id, reason="test")
    assert replacement.params == {"min_strength": "0.2"}
    assert replacement.accepts(signal(SignalSource.TRADINGVIEW, "tv", strength=0.5))
    assert not replacement.accepts(signal(SignalSource.TRADINGVIEW, "tv", strength=0.1))
    assert not replacement.accepts(signal(SignalSource.PROTO_STRATEGY, "proto-ema-cross-1m"))


# --- eliminated ids never reach the Risk Engine ------------------------------------------


async def test_an_eliminated_agent_intent_is_dropped_before_the_risk_engine(monkeypatch) -> None:
    roster = AgentRoster()
    await roster.seed()
    agent = roster.active_for(tv_slot())
    await roster.eliminate(agent.agent_id, reason="test")
    monkeypatch.setattr(roster_surface, "_roster", roster)

    async def must_not_assess(_intent):
        raise AssertionError("an eliminated agent's intent reached the Risk Engine")

    monkeypatch.setattr(agent_surface, "assess_risk", must_not_assess)
    proposal = intent("BUY", "0.0005", "62000")
    assert await agent_surface.submit_for_agent(agent.agent_id, proposal) == (None, None)
    assert await agent_surface.submit_for_agent("AG-9999-NOBODY", proposal) == (None, None)
    assert roster.agent_for_intent(str(proposal.intent_id)) is None
    with pytest.raises(AgentEliminated):
        await roster.attribute(
            str(proposal.intent_id), agent.agent_id, instrument_id=BTC, side="BUY"
        )


# --- books, attribution, P&L -------------------------------------------------------------


async def test_fills_are_attributed_once_with_exact_pnl(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'roster.db'}")
    journal = Journal()
    try:
        roster = AgentRoster(engine, recorder=journal)
        await roster.seed()
        agent = roster.active_for(tv_slot())
        buy = str(uuid4())
        await roster.attribute(buy, agent.agent_id, instrument_id=BTC, side="BUY")

        partial = order(buy, "BUY", "0.0004", "60000", "0.024")
        assert len(await roster.apply_order(partial)) == 1
        assert await roster.apply_order(partial) == []  # the same fill twice: no change
        full = order(buy, "BUY", "0.001", "60000", "0.06")
        [delta] = await roster.apply_order(full)
        assert delta["quantity"] == "0.0006" and delta["agent_id"] == agent.agent_id
        assert await roster.apply_order(full) == []
        assert agent.book.quantity == Decimal("0.001")
        assert agent.book.cost == Decimal("60.0000")
        assert agent.book.fees == Decimal("0.060")

        # Marked at 61,000: +1 unrealized - 0.06 fees = 0.94 USDT = R$5.17 at 5.5.
        assert agent.equity_brl(Decimal("61000"), FX) == Decimal("205.1700")
        assert agent.equity_brl(None, FX) is None  # no mark: unknown, never guessed
        assert agent.equity_brl(Decimal("61000"), None) is None  # no FX: unknown

        sell = str(uuid4())
        await roster.attribute(sell, agent.agent_id, instrument_id=BTC, side="SELL")
        [closed] = await roster.apply_order(order(sell, "SELL", "0.001", "62000", "0.062"))
        assert Decimal(closed["realized_pnl"]) == Decimal("2")
        assert agent.book.quantity == 0 and agent.book.cost == 0
        assert agent.book.realized_pnl == Decimal("2.0000")
        # 2 realized - 0.122 fees = 1.878 USDT = R$10.329.
        assert agent.equity_brl(None, FX) == Decimal("210.32900")
        assert journal.types().count("AGENT_TRADE_ATTRIBUTED") == 3

        # The books and applied fills are durable: a restart re-applies nothing.
        restarted = AgentRoster(engine)
        await restarted.load()
        again = restarted.get(agent.agent_id)
        assert again.book.realized_pnl == Decimal("2.0000")
        assert again.book.fees == Decimal("0.122")
        assert await restarted.apply_order(order(sell, "SELL", "0.001", "62000", "0.062")) == []
        assert restarted.agent_for_intent(buy) == agent.agent_id
        assert await restarted.apply_order(order(str(uuid4()), "BUY", "1", "1", "0")) == []
    finally:
        await engine.dispose()


async def test_a_fee_charged_in_coins_leaves_the_position_at_cost() -> None:
    roster = AgentRoster()
    await roster.seed()
    agent = roster.active_for(tv_slot())
    buy = str(uuid4())
    await roster.attribute(buy, agent.agent_id, instrument_id=BTC, side="BUY")
    instrument = PAPER_INSTRUMENTS[BTC]
    await roster.apply_order(
        order(buy, "BUY", "0.001", "60000", "0.00001", fee_asset="BTC"), instrument=instrument
    )
    assert agent.book.quantity == Decimal("0.00099")
    assert agent.book.fees == Decimal("0.6")  # 0.00001 BTC at the 60,000 average cost
    assert agent.book.pnl(Decimal("60000")) == Decimal("-0.6")


# --- survival instinct -------------------------------------------------------------------


def test_survival_multiplier_and_rising_min_score() -> None:
    assert size_multiplier(Decimal("200")) == 1
    assert size_multiplier(Decimal("400")) == 1  # gains never enlarge
    assert size_multiplier(Decimal("100")) == Decimal("0.25")
    assert size_multiplier(Decimal("-5")) == 0
    assert entry_min_score(0.6, Decimal("200")) == pytest.approx(0.6)
    assert entry_min_score(0.6, Decimal("400")) == pytest.approx(0.6)  # never lowered
    assert entry_min_score(0.6, Decimal("100")) == pytest.approx(0.75)
    assert entry_min_score(0.6, Decimal("0")) == pytest.approx(0.9)
    assert entry_min_score(0.9, Decimal("0")) == 1.0


def agent_with(quantity: str = "0", cost: str = "0") -> TraderAgent:
    return TraderAgent(
        agent_id="AG-0001-ORION",
        sequence=1,
        name="ORION",
        slot=tv_slot(SOL),
        params={},
        capital_brl=Decimal("200"),
        status=AgentStatus.ACTIVE,
        generation=0,
        created_at=datetime.now(UTC),
        book=AgentBook(quantity=Decimal(quantity), cost=Decimal(cost)),
    )


def test_an_exit_sells_only_the_agents_own_position() -> None:
    sol = PAPER_INSTRUMENTS[SOL]
    # The account holds 4 faucet SOL plus the agent's 0.12: the agent sells its 0.12 only.
    proposal = intent("SELL", "4.12", "150.00", SOL)
    shaped = shape_for_agent(
        proposal,
        agent=agent_with("0.12", "18"),
        instrument=sol,
        equity_brl=Decimal("200"),
        brl_per_quote=Decimal("5"),
    )
    assert shaped.intent is not None and shaped.intent.quantity == Decimal("0.12")
    assert shaped.intent.intent_id == proposal.intent_id and shaped.intent.side == "SELL"

    nothing = shape_for_agent(
        proposal,
        agent=agent_with(),
        instrument=sol,
        equity_brl=Decimal("200"),
        brl_per_quote=Decimal("5"),
    )
    assert nothing.intent is None and nothing.reason == "agent holds no position to sell"


def test_a_buy_is_capped_by_the_agents_equity_and_shrunk_by_survival() -> None:
    sol = PAPER_INSTRUMENTS[SOL]
    proposal = intent("BUY", "10", "100.00", SOL)  # 1,000 USDT asked for
    full = shape_for_agent(
        proposal,
        agent=agent_with(),
        instrument=sol,
        equity_brl=Decimal("200"),
        brl_per_quote=Decimal("5"),
    )
    # R$200 at 5 BRL/USDT = 40 USDT of exposure at most: 0.4 SOL at 100.
    assert full.intent.quantity == Decimal("0.400")

    small = intent("BUY", "0.2", "100.00", SOL)
    hurt = shape_for_agent(
        small,
        agent=agent_with(),
        instrument=sol,
        equity_brl=Decimal("100"),
        brl_per_quote=Decimal("5"),
    )
    assert hurt.intent.quantity == Decimal("0.050")  # 0.2 x (100/200)^2

    held = shape_for_agent(
        proposal,
        agent=agent_with("0.4", "40"),
        instrument=sol,
        equity_brl=Decimal("200"),
        brl_per_quote=Decimal("5"),
    )
    assert held.intent is None and held.reason == "agent exposure cap reached"

    tiny = shape_for_agent(
        small,
        agent=agent_with(),
        instrument=sol,
        equity_brl=Decimal("40"),
        brl_per_quote=Decimal("5"),
    )
    assert tiny.intent is None  # 0.2 x 0.04 = 0.008 SOL = 0.8 USDT: below the venue minimum


# --- elimination, replacement and the allocation cap -------------------------------------


async def test_an_agent_below_the_minimum_order_is_eliminated_and_replaced(monkeypatch) -> None:
    journal = Journal()
    roster = AgentRoster(recorder=journal)
    await roster.seed()
    monkeypatch.setattr(roster_surface, "_roster", roster)
    agent = roster.active_for(ema_slot())
    buy = str(uuid4())
    await roster.attribute(buy, agent.agent_id, instrument_id=BTC, side="BUY")
    await roster.apply_order(order(buy, "BUY", "0.0006", "60000", "0"))  # 36 USDT

    instrument = PAPER_INSTRUMENTS[BTC]
    healthy = roster_surface.Valuation(agent.equity_brl(Decimal("60000"), FX), FX, None)
    assert await roster_surface.survive(agent, healthy, instrument)

    # BTC collapses to 10,000: -30 USDT = -R$165 -> R$35... still above R$30.
    still = agent.equity_brl(Decimal("10000"), FX)
    assert still == Decimal("35.0000")
    assert await roster_surface.survive(
        agent, roster_surface.Valuation(still, FX, None), instrument
    )
    gone = agent.equity_brl(Decimal("5000"), FX)  # R$18.50
    assert not await roster_surface.survive(
        agent, roster_surface.Valuation(gone, FX, None), instrument
    )
    assert agent.status is AgentStatus.ELIMINATED
    assert "below the minimum order R$30.00" in agent.elimination_reason
    replacement = roster.active_for(ema_slot())
    assert replacement.agent_id != agent.agent_id and replacement.generation == 1
    assert replacement.params == {"fast": 8, "slow": 21}
    assert roster_surface.ema_params(BTC) == (8, 21)
    assert journal.types()[-2:] == ["AGENT_ELIMINATED", "AGENT_SPAWNED"]
    assert journal.events[-1][1]["replaces"] == agent.agent_id

    # Unknown equity never eliminates: the agent abstains instead.
    assert await roster_surface.survive(
        replacement, roster_surface.Valuation(None, None, None), instrument
    )
    assert replacement.active


async def test_allocated_capital_never_exceeds_the_reference_capital() -> None:
    slots = [Slot(SlotKind.TRADINGVIEW, f"BINANCE_SPOT:X{i}USDT") for i in range(51)]
    roster = AgentRoster(slots=slots)
    spawned = await roster.seed()
    assert len(spawned) == 50
    assert roster.allocated() == REFERENCE_CAPITAL
    assert roster.active_for(slots[50]) is None
    with pytest.raises(AllocationRefused):
        await roster.spawn(slots[50])
    # An elimination frees R$200, which its replacement takes: never above the cap.
    _, replacement = await roster.eliminate(spawned[0].agent_id, reason="test")
    assert replacement is not None and roster.allocated() == REFERENCE_CAPITAL


# --- journal and floor -------------------------------------------------------------------


def test_floor_shows_spawns_eliminations_and_attributions() -> None:
    def event(kind: str, payload: dict) -> dict:
        return {"sequence": 1, "timestamp": "t", "event_type": kind, "payload": payload}

    slot = tv_slot().to_dict()
    [spawned] = floor_surface.floor_actions(
        event(
            "AGENT_SPAWNED",
            {
                "agent_id": "AG-0007-SIRIUS",
                "slot": slot,
                "generation": 1,
                "capital_brl": "200",
                "replaces": "AG-0001-ORION",
            },
        )
    )
    assert spawned["station"] == "fusion" and "AG-0007-SIRIUS" in spawned["headline"]
    assert "replaces AG-0001-ORION" in spawned["why"]
    [eliminated] = floor_surface.floor_actions(
        event(
            "AGENT_ELIMINATED",
            {"agent_id": "AG-0001-ORION", "slot": slot, "elimination_reason": "equity low"},
        )
    )
    assert eliminated["tone"] == "alert" and eliminated["symbol"] == "BTCUSDT"
    [traded] = floor_surface.floor_actions(
        event(
            "AGENT_TRADE_ATTRIBUTED",
            {
                "agent_id": "AG-0002-VEGA",
                "side": "BUY",
                "quantity": "0.001",
                "instrument_id": BTC,
                "realized_pnl": "0",
            },
        )
    )
    assert traded["station"] == "oms" and "AG-0002-VEGA" in traded["headline"]


def test_roster_migration_matches_the_runtime_tables(monkeypatch) -> None:
    path = Path("migrations/versions/0012_agent_roster.py")
    spec = importlib.util.spec_from_file_location("migration_0012", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0011_live_tick_sequence_bigint"
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        found = inspect(connection)
        for table in (agents_table, agent_orders_table):
            columns = {c["name"] for c in found.get_columns(table.name)}
            assert columns == set(table.columns.keys())
        migration.downgrade()
        assert "agent_roster" not in inspect(connection).get_table_names()


# --- surface: read-only roster and a routed, attributed trade ------------------------------


def configure(monkeypatch, **overrides) -> Settings:
    values = {
        "tradingview_webhook_secret": SECRET,
        "tradingview_ip_allowlist_enabled": False,
        "tradingview_instruments": BTC,
        "live_market_source": "BINANCE",
        "agent_pipeline_enabled": True,
        "agent_roster_enabled": True,
        "oms_paper_execution_enabled": True,
        "oms_paper_slippage_bps": 0.0,
    }
    values.update(overrides)
    configured = Settings(**values)
    for module in (main, signal_surface, agent_surface, risk_surface, oms_surface, roster_surface):
        monkeypatch.setattr(module, "settings", configured)
    return configured


def live_snapshot(symbol: str):
    return {
        "symbol": symbol,
        "venue": "BINANCE",
        "bid": 62_000.00,
        "ask": 62_000.20,
        "timestamp": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }


@pytest.fixture
def surfaces(monkeypatch):
    monkeypatch.setattr(agent_surface.live_monitor, "snapshot", live_snapshot)
    resets = (
        signal_surface.reset_signal_state,
        agent_surface.reset_agent_state,
        risk_surface.reset_risk_state,
        oms_surface.reset_oms_state,
        roster_surface.reset_roster_state,
    )
    for reset in resets:
        reset()
    yield
    for reset in resets:
        reset()


def test_roster_route_is_labelled_virtual_and_read_only(monkeypatch, surfaces) -> None:
    configure(monkeypatch)

    async def no_fx(_quote):
        raise AssertionError("an empty book needs no FX")

    monkeypatch.setattr(roster_surface, "brl_per", no_fx)
    with TestClient(main.app) as client:
        body = client.get("/v1/agents/roster").json()
        assert client.post("/v1/agents/roster").status_code == 405
    assert body["financial_connectivity"] is False
    assert body["real_money_execution"] is False
    assert body["capital"] == "VIRTUAL" and body["currency"] == "BRL"
    assert body["data_mode"] == "PAPER"
    assert body["totals"]["active"] == 6 and body["totals"]["eliminated"] == 0
    assert body["totals"]["allocated_capital_brl"] == "1200"
    assert body["totals"]["reference_capital_brl"] == "10000"
    first = body["agents"][0]
    assert first["agent_id"] == "AG-0001-ORION" and first["status"] == "ACTIVE"
    assert first["equity_brl"] == "200" and first["valuation"] == "MARKED"


async def test_a_signal_trades_on_its_agents_book_and_is_attributed(monkeypatch, surfaces) -> None:
    configure(monkeypatch)

    async def fx(_quote):
        return FX

    monkeypatch.setattr(roster_surface, "brl_per", fx)
    tv = signal(SignalSource.TRADINGVIEW, "tv-ema")
    agent_surface.buffer_signal(tv)
    before = operational_journal.journal.head_sequence

    decision, risk, placed = await agent_surface._evaluate_agent(tv_slot())

    roster = roster_surface.roster()
    agent = roster.active_for(tv_slot())
    assert decision is not None and risk is not None and risk.approved
    assert placed is not None and placed["status"] == "FILLED" and placed["side"] == "BUY"
    # R$200 at 5.5 BRL/USDT is 36.36 USDT: far below the 10,000 USDT account's own sizing.
    notional = Decimal(placed["filled_quantity"]) * Decimal(placed["average_price"])
    assert Decimal("5") <= notional <= Decimal("200") / FX
    assert roster.agent_for_intent(placed["intent_id"]) == agent.agent_id
    assert agent.book.quantity == Decimal(placed["filled_quantity"])
    assert agent.book.fees == Decimal(placed["fee"])
    events = [e for e in operational_journal.journal.recent(50) if e.sequence > before]
    kinds = [e.event_type for e in events]
    assert "AGENT_TRADE_ATTRIBUTED" in kinds
    decided = next(e for e in events if e.event_type == "AGENT_DECISION")
    assert decided.payload["agent_id"] == agent.agent_id

    # Reading the orders again attributes nothing twice.
    assert await roster_surface.sync_fills()
    assert agent.book.quantity == Decimal(placed["filled_quantity"])

    # A second LONG is capped by the agent's own exposure, not the account's.
    again = signal(SignalSource.TRADINGVIEW, "tv-ema")
    agent_surface.buffer_signal(again)
    second, _, second_order = await agent_surface._evaluate_agent(tv_slot())
    assert second_order is None
    assert second.outcome.value == "ABSTAINED"


async def test_no_fx_rate_means_the_agent_abstains(monkeypatch, surfaces) -> None:
    configure(monkeypatch)

    async def no_fx(_quote):
        return None

    monkeypatch.setattr(roster_surface, "brl_per", no_fx)
    roster = await roster_surface.ready()
    agent = roster.active_for(tv_slot())
    buy = str(uuid4())
    await roster.attribute(buy, agent.agent_id, instrument_id=BTC, side="BUY")
    await roster.apply_order(order(buy, "BUY", "0.0001", "60000", "0.006"))

    async def must_not_assess(_intent):
        raise AssertionError("no FX rate: the agent must abstain")

    monkeypatch.setattr(agent_surface, "assess_risk", must_not_assess)
    agent_surface.buffer_signal(signal(SignalSource.TRADINGVIEW, "tv-ema"))
    assert await agent_surface._evaluate_agent(tv_slot()) == (None, None, None)
    assert agent.active


def test_the_tradingview_webhook_routes_to_the_slot_agent(monkeypatch, surfaces) -> None:
    configure(monkeypatch)

    async def fx(_quote):
        return FX

    monkeypatch.setattr(roster_surface, "brl_per", fx)
    alert = json.dumps(
        {
            "secret": SECRET,
            "alert_id": f"a-{time.time_ns()}",
            "strategy_id": "tv-ema",
            "instrument_id": BTC,
            "direction": "LONG",
            "strength": 0.9,
            "timeframe": "15",
            "fired_at": datetime.now(UTC).isoformat(),
        }
    )
    with TestClient(main.app) as client:
        assert client.post("/v1/signals/tradingview", content=alert).status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            orders = client.get("/v1/oms/orders").json()["orders"]
            if orders:
                break
            time.sleep(0.01)
        roster = client.get("/v1/agents/roster").json()
    [placed] = orders
    tv_agent = next(a for a in roster["agents"] if a["slot"]["key"] == f"tradingview|{BTC}")
    assert tv_agent["book"]["position_quantity"] == placed["filled_quantity"]
    assert Decimal(tv_agent["equity_brl"]) < Decimal("200")  # fees paid, marked at the bid side


async def test_a_replacement_ema_agent_trades_its_own_parameters(monkeypatch, surfaces) -> None:
    from apps.api.app import strategy_surface

    configure(monkeypatch)
    monkeypatch.setattr(strategy_surface, "settings", roster_surface.settings)
    strategy_surface.reset_strategy_state()
    roster = await roster_surface.ready()
    assert strategy_surface._strategy_for(BTC) is strategy_surface.strategy  # 5/20 default
    await roster.eliminate(roster.active_for(ema_slot()).agent_id, reason="test")
    replaced = strategy_surface._strategy_for(BTC)
    assert (replaced.policy.fast, replaced.policy.slow) == (8, 21)
    assert strategy_surface._strategy_for(SOL) is strategy_surface.strategy
    strategy_surface.reset_strategy_state()


async def test_lab_strategies_stay_off_unless_flagged(monkeypatch, surfaces) -> None:
    from apps.api.app import strategy_surface
    from services.agents import roster as roster_module
    from services.research.live import LabSignalStrategy

    spec = {"strategy": "ts_momentum", "lookback": 3, "vol_period": 5}
    monkeypatch.setattr(
        roster_module, "LAB_PARAM_LADDER", {BTC: ({"spec": spec, "timeframe_minutes": 60},)}
    )
    configure(monkeypatch)
    monkeypatch.setattr(strategy_surface, "settings", roster_surface.settings)
    strategy_surface.reset_strategy_state()
    await roster_surface.ready()
    assert strategy_surface._strategy_for(BTC) is strategy_surface.strategy  # flag off: EMA
    assert roster_surface.lab_params(BTC) is None

    roster_surface.reset_roster_state()
    configure(monkeypatch, agent_lab_strategies_enabled=True, agent_lab_warmup_klines=True)
    monkeypatch.setattr(strategy_surface, "settings", roster_surface.settings)
    await roster_surface.ready()
    chosen = strategy_surface._strategy_for(BTC)
    assert isinstance(chosen, LabSignalStrategy) and chosen.spec == spec
    assert strategy_surface._strategy_for(BTC) is chosen  # one adapter per lab strategy
    assert strategy_surface._strategy_for(SOL) is strategy_surface.strategy  # no ladder there
    assert roster_surface.ema_params(BTC) is None

    warmed = []
    monkeypatch.setattr(
        strategy_surface, "lab_klines", lambda i, m, n: warmed.append((i, m, n)) or []
    )
    monkeypatch.setattr(
        strategy_surface,
        "settings",
        roster_surface.settings.model_copy(update={"agent_lab_warmup_klines": False}),
    )
    await strategy_surface._warm_lab(chosen, BTC)  # warm-up flag off: no fetch
    assert warmed == []
    monkeypatch.setattr(
        strategy_surface,
        "settings",
        roster_surface.settings.model_copy(update={"agent_lab_warmup_klines": True}),
    )
    await strategy_surface._warm_lab(chosen, BTC)
    await strategy_surface._warm_lab(chosen, BTC)  # once per instrument
    assert warmed == [(BTC, 60, chosen.history)]
    strategy_surface.reset_strategy_state()


async def test_xp_and_alpaca_fills_reach_their_agents_books(monkeypatch, surfaces) -> None:
    from apps.api.app import instrument_registry
    from apps.api.app.executor_client import ExecutorUnavailable

    configured = configure(
        monkeypatch,
        oms_execution_backend="executor",
        executor_url="http://binance-executor",
        executor_api_token="b" * 32,
        xp_executor_url="http://xp-executor",
        xp_executor_api_token="x" * 32,
        alpaca_executor_url="http://alpaca-executor",
        alpaca_executor_api_token="a" * 32,
        risk_permit_signing_key="k" * 32,
        agent_b3_enabled=True,
        agent_us_enabled=True,
    )
    monkeypatch.setattr(instrument_registry, "settings", configured)
    roster = await roster_surface.ready()
    spy = roster.active_for(Slot(SlotKind.EMA, "ALPACA_US:SPY"))
    petr = roster.active_for(Slot(SlotKind.EMA, "XP_B3:PETR4F"))
    assert spy is not None and petr is not None
    buy = str(uuid4())
    await roster.attribute(buy, spy.agent_id, instrument_id="ALPACA_US:SPY", side="BUY")
    read: list[VenueId] = []

    async def recent_orders(_limit, venue=VenueId.BINANCE_SPOT):
        read.append(venue)
        if venue is VenueId.XP_B3:  # the XP node is off outside its schedule
            raise ExecutorUnavailable("xp executor unreachable")
        if venue is VenueId.ALPACA_US:
            return [order(buy, "BUY", "1", "500", "0", instrument_id="ALPACA_US:SPY")]
        return []

    monkeypatch.setattr(oms_surface, "recent_orders", recent_orders)

    synced = await roster_surface.sync_venues()
    # An unreachable XP node is reported for XP only and never hides the Alpaca fill.
    assert synced == {VenueId.BINANCE_SPOT: True, VenueId.ALPACA_US: True, VenueId.XP_B3: False}
    assert spy.book.quantity == Decimal("1") and spy.book.cost == Decimal("500")
    assert petr.book.empty
    assert not await roster_surface.sync_fills()
    assert spy.book.quantity == Decimal("1")  # read twice, applied once

    # A narrowed sync reads only the venue asked for.
    read.clear()
    assert await roster_surface.sync_fills([VenueId.ALPACA_US])
    assert read == [VenueId.ALPACA_US]


async def test_paper_mode_has_no_xp_or_alpaca_orders_to_read(monkeypatch, surfaces) -> None:
    configure(monkeypatch)
    assert await oms_surface.recent_orders(10, venue=VenueId.XP_B3) == []
    assert await oms_surface.recent_orders(10, venue=VenueId.ALPACA_US) == []
