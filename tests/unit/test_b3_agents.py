"""Two own-strategy agents on B3 (XP demo): off unless flagged and XP routing is configured."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from apps.api.app import b3_quotes, roster_surface, strategy_surface
from apps.api.app.settings import Settings
from services.agents.roster import (
    AGENT_STARTING_CAPITAL,
    B3_SLOTS,
    INITIAL_SLOTS,
    AgentBook,
    AgentRoster,
    AgentStatus,
    SlotKind,
    TraderAgent,
    shape_for_agent,
)
from services.execution import OrderType, TimeInForce, TradeIntent, VenueId
from services.execution.catalog import B3_INSTRUMENTS
from services.execution.venue import Quote

PETR = "XP_B3:PETR4F"
XP_ON = {
    "oms_execution_backend": "executor",
    "executor_url": "http://binance-executor:8100",
    "executor_api_token": "e" * 32,
    "xp_executor_url": "http://xp-executor:8100",
    "xp_executor_api_token": "x" * 32,
    "risk_permit_signing_key": "r" * 32,
}


def configure(monkeypatch, **values) -> Settings:
    configured = Settings(**values)
    for module in (roster_surface, strategy_surface):
        monkeypatch.setattr(module, "settings", configured)
    return configured


def test_b3_agents_need_the_flag_and_the_xp_node(monkeypatch) -> None:
    configure(monkeypatch)
    assert roster_surface.roster_slots() == INITIAL_SLOTS
    configure(monkeypatch, agent_b3_enabled=True)  # flag without the XP node: nothing changes
    assert roster_surface.roster_slots() == INITIAL_SLOTS
    assert strategy_surface.instruments() == strategy_surface._INSTRUMENTS
    configure(monkeypatch, **XP_ON)  # XP node without the flag: nothing changes
    assert roster_surface.roster_slots() == INITIAL_SLOTS

    configure(monkeypatch, agent_b3_enabled=True, **XP_ON)
    assert roster_surface.roster_slots() == INITIAL_SLOTS + B3_SLOTS
    assert strategy_surface.instruments()[-2:] == ("XP_B3:PETR4F", "XP_B3:VALE3F")


def test_the_b3_slots_are_own_strategy_agents_on_catalog_symbols() -> None:
    assert len(B3_SLOTS) == 2
    for slot in B3_SLOTS:
        assert slot.kind is SlotKind.EMA
        assert slot.instrument_id in B3_INSTRUMENTS
        assert B3_INSTRUMENTS[slot.instrument_id].quote_asset == "BRL"


async def test_seeding_gives_the_two_b3_agents_r200_each_within_the_reference() -> None:
    roster = AgentRoster(slots=INITIAL_SLOTS + B3_SLOTS)
    spawned = await roster.seed()
    assert len(spawned) == 8
    b3 = [roster.active_for(slot) for slot in B3_SLOTS]
    assert all(a is not None and a.capital_brl == AGENT_STARTING_CAPITAL for a in b3)
    assert all({"fast", "slow"} <= set(a.params) for a in b3)
    assert roster.allocated() == AGENT_STARTING_CAPITAL * 8
    assert await roster.seed() == []  # idempotent


def _intent(side: str, quantity: str, price: str) -> TradeIntent:
    return TradeIntent(
        intent_id=uuid4(),
        instrument_id=PETR,
        venue=VenueId.XP_B3,
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


def _agent(quantity: str = "0", cost: str = "0") -> TraderAgent:
    return TraderAgent(
        agent_id="AG-0007-VEGA",
        sequence=7,
        name="VEGA",
        slot=B3_SLOTS[0],
        params={"fast": 5, "slow": 20},
        capital_brl=Decimal("200"),
        status=AgentStatus.ACTIVE,
        generation=0,
        created_at=datetime.now(UTC),
        book=AgentBook(quantity=Decimal(quantity), cost=Decimal(cost)),
    )


def test_a_b3_agent_trades_mini_whole_share_lots_in_brl() -> None:
    petr = B3_INSTRUMENTS[PETR]
    # The account's sizing asks for 100 shares; R$200 at R$38.50 buys 5 whole shares at most.
    buy = shape_for_agent(
        _intent("BUY", "100", "38.50"),
        agent=_agent(),
        instrument=petr,
        equity_brl=Decimal("200"),
        brl_per_quote=Decimal("1"),
    )
    assert buy.intent is not None and buy.intent.quantity == Decimal("5")
    # The exit sells only the agent's own shares.
    sell = shape_for_agent(
        _intent("SELL", "40", "38.40"),
        agent=_agent("5", "192.50"),
        instrument=petr,
        equity_brl=Decimal("199"),
        brl_per_quote=Decimal("1"),
    )
    assert sell.intent is not None and sell.intent.quantity == Decimal("5")


class _Feed:
    def __init__(self, quotes: dict[str, Quote]) -> None:
        self.quotes = quotes

    def quote(self, symbol: str) -> Quote | None:
        return self.quotes.get(symbol)


async def test_the_strategy_reads_b3_quotes_and_ignores_stale_ones(monkeypatch) -> None:
    configure(monkeypatch, agent_b3_enabled=True, live_market_source="COINBASE", **XP_ON)
    strategy_surface.reset_strategy_state()
    now = datetime.now(UTC)
    fresh = Quote(bid=Decimal("38.40"), ask=Decimal("38.42"), observed_at=now)
    b3_quotes.install(_Feed({"PETR4F": fresh}))
    try:
        await strategy_surface.tick(now)
        assert strategy_surface._counters["ticks"] == 1  # PETR4F fed; VALE3F has no quote
        # After the close the terminal's last tick ages out: nothing is fed.
        stale = Quote(bid=fresh.bid, ask=fresh.ask, observed_at=now - timedelta(minutes=5))
        b3_quotes.install(_Feed({"PETR4F": stale, "VALE3F": stale}))
        await strategy_surface.tick(now)
        assert strategy_surface._counters["ticks"] == 1
    finally:
        b3_quotes.install(None)
        strategy_surface.reset_strategy_state()
