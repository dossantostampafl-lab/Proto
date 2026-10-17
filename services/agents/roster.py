"""Trader-agent roster: each agent trades a virtual R$200 and is eliminated when it is gone.

An agent owns one *slot* (a signal kind on one instrument): PROTO's own EMA-cross strategy or
TradingView alerts, on BTCUSDT, ETHUSDT or SOLUSDT. Its code (``AG-0007-ORION``) comes from a
monotonic sequence and is never reused, so an eliminated code can never trade again.

Agents keep a virtual book in the instrument's quote currency (cost, realized P&L and fees of
the fills attributed to them); equity is valued in BRL as ``200 + P&L x BRL-per-quote``. The
capital is virtual: it is a sizing budget inside the testnet/paper account, never money.

Survival instinct only *tightens*: an agent's exposure is capped by its own equity, its size
shrinks with ``(equity/200)^2`` and its entry bar rises as equity falls. Every global check
(constitutional limits, Risk Engine permit, OMS) still applies on top. Nothing here can place
an order: agents still produce proposals for the Risk Engine.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from services.execution import Signal, SignalSource, TradableInstrument, TradeIntent
from services.execution.constitution import MAX_GROSS_LEVERAGE, REFERENCE_CAPITAL

logger = logging.getLogger("proto.agents.roster")

AGENT_STARTING_CAPITAL = Decimal("200")  # BRL, virtual
# Conservative minimum order in BRL: an agent below it could not place even the smallest order.
ELIMINATION_FLOOR_BRL = Decimal("30")
# Entry bar rises by up to this much as equity falls from R$200 to zero.
MIN_SCORE_SURVIVAL_SLOPE = Decimal("0.3")
# Tighten only: never more than the constitution's gross leverage, and never above 1.0x.
AGENT_MAX_LEVERAGE = min(Decimal("1.0"), MAX_GROSS_LEVERAGE)

EMA_STRATEGY_ID = "proto-ema-cross-1m"  # services.signals.proto_strategy.STRATEGY_ID

NAMES = (
    "ORION",
    "VEGA",
    "LYRA",
    "ATLAS",
    "NOVA",
    "RIGEL",
    "SIRIUS",
    "ALTAIR",
    "DENEB",
    "CASTOR",
    "POLLUX",
    "ANTARES",
    "CAPELLA",
    "MIRA",
    "ELARA",
    "CORVUS",
    "DRACO",
    "AQUILA",
    "CYGNUS",
    "PERSEUS",
)
# Replacement n of an EMA slot trades the n-th (fast, slow) pair; the list cycles when exhausted.
EMA_PARAM_LADDER: tuple[tuple[int, int], ...] = (
    (5, 20),
    (8, 21),
    (12, 26),
    (3, 13),
    (10, 30),
    (6, 18),
    (4, 16),
    (15, 40),
)
# Replacement n of a TradingView slot ignores alerts weaker than the n-th strength.
TRADINGVIEW_MIN_STRENGTH_LADDER: tuple[str, ...] = ("0", "0.2", "0.4", "0.5", "0.6", "0.7")

_BOUNDARY = {
    "capital": "VIRTUAL",
    "financial_connectivity": False,
    "real_money_execution": False,
}


class SlotKind(StrEnum):
    EMA = EMA_STRATEGY_ID
    TRADINGVIEW = "tradingview"


class AgentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ELIMINATED = "ELIMINATED"


@dataclass(frozen=True)
class Slot:
    kind: SlotKind
    instrument_id: str

    @property
    def key(self) -> str:
        return f"{self.kind.value}|{self.instrument_id}"

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "instrument_id": self.instrument_id, "key": self.key}


INITIAL_SLOTS: tuple[Slot, ...] = tuple(
    Slot(kind, f"BINANCE_SPOT:{symbol}")
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    for kind in (SlotKind.EMA, SlotKind.TRADINGVIEW)
)


def slot_of(signal: Signal) -> Slot | None:
    """The slot a signal belongs to, or None when no agent trades that kind of signal."""

    if signal.source is SignalSource.PROTO_STRATEGY and signal.strategy_id == EMA_STRATEGY_ID:
        return Slot(SlotKind.EMA, signal.instrument_id)
    if signal.source is SignalSource.TRADINGVIEW:
        return Slot(SlotKind.TRADINGVIEW, signal.instrument_id)
    return None


def params_for(kind: SlotKind, generation: int) -> dict[str, Any]:
    """Deterministic parameters of the ``generation``-th agent of a slot."""

    if kind is SlotKind.EMA:
        fast, slow = EMA_PARAM_LADDER[generation % len(EMA_PARAM_LADDER)]
        return {"fast": fast, "slow": slow}
    return {
        "min_strength": TRADINGVIEW_MIN_STRENGTH_LADDER[
            generation % len(TRADINGVIEW_MIN_STRENGTH_LADDER)
        ]
    }


# --- survival instinct (tighten only) ---------------------------------------------------


def survival_ratio(equity_brl: Decimal) -> Decimal:
    """Equity over the starting capital, clamped to [0, 1]: gains never loosen anything."""

    ratio = equity_brl / AGENT_STARTING_CAPITAL
    return min(max(ratio, Decimal("0")), Decimal("1"))


def size_multiplier(equity_brl: Decimal) -> Decimal:
    """(equity / 200)^2 clamped to [0, 1]."""

    ratio = survival_ratio(equity_brl)
    return ratio * ratio


def entry_min_score(base_min_score: float, equity_brl: Decimal) -> float:
    """base + 0.3 x (1 - equity/200), never below base and never above 1."""

    raised = Decimal(str(base_min_score)) + MIN_SCORE_SURVIVAL_SLOPE * (
        1 - survival_ratio(equity_brl)
    )
    return float(min(max(raised, Decimal(str(base_min_score))), Decimal("1")))


def elimination_floor(min_notional_quote: Decimal, brl_per_quote: Decimal) -> Decimal:
    """The smallest order the agent could place, in BRL (never below R$30)."""

    return max(ELIMINATION_FLOOR_BRL, min_notional_quote * brl_per_quote)


# --- virtual book ------------------------------------------------------------------------


@dataclass
class AgentBook:
    """Quote-currency book of the fills attributed to one agent."""

    quantity: Decimal = Decimal("0")
    cost: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")

    @property
    def empty(self) -> bool:
        return not (self.quantity or self.cost or self.realized_pnl or self.fees)

    def _release(self, quantity: Decimal) -> Decimal:
        """Remove ``quantity`` from the position at average cost; returns that cost."""

        quantity = min(quantity, self.quantity)
        if quantity <= 0:
            return Decimal("0")
        if quantity == self.quantity:
            released = self.cost
        else:
            released = self.cost * quantity / self.quantity
        self.quantity -= quantity
        self.cost -= released
        return released

    def apply_fill(
        self,
        side: str,
        quantity: Decimal,
        notional: Decimal,
        *,
        fee_quote: Decimal = Decimal("0"),
        fee_base: Decimal = Decimal("0"),
    ) -> Decimal:
        """Apply one fill delta; returns the realized P&L it produced (before fees)."""

        realized = Decimal("0")
        if side == "BUY":
            self.quantity += quantity
            self.cost += notional
        else:
            sold = min(quantity, self.quantity)
            if sold < quantity:
                logger.warning("agent sold %s more than it held; ignored", quantity - sold)
            proceeds = notional if sold == quantity else notional * sold / quantity
            realized = proceeds - self._release(sold)
            self.realized_pnl += realized
        self.fees += fee_quote
        if fee_base > 0:
            # A fee taken in coins removes them from the position at their average cost.
            self.fees += self._release(fee_base)
        return realized

    def unrealized(self, mark: Decimal | None) -> Decimal | None:
        if self.quantity == 0:
            return -self.cost if self.cost else Decimal("0")
        if mark is None:
            return None
        return self.quantity * mark - self.cost

    def pnl(self, mark: Decimal | None) -> Decimal | None:
        """Net P&L in the quote currency: realized - fees + unrealized (None: unmarked)."""

        unrealized = self.unrealized(mark)
        if unrealized is None:
            return None
        return self.realized_pnl - self.fees + unrealized


@dataclass
class TraderAgent:
    agent_id: str
    sequence: int
    name: str
    slot: Slot
    params: dict[str, Any]
    capital_brl: Decimal
    status: AgentStatus
    generation: int
    created_at: datetime
    eliminated_at: datetime | None = None
    elimination_reason: str | None = None
    book: AgentBook = field(default_factory=AgentBook)

    @property
    def active(self) -> bool:
        return self.status is AgentStatus.ACTIVE

    def equity_brl(self, mark: Decimal | None, brl_per_quote: Decimal | None) -> Decimal | None:
        """Capital + P&L converted to BRL. None when a mark or the FX rate is missing."""

        if self.book.empty:
            return self.capital_brl
        pnl = self.book.pnl(mark)
        if pnl is None:
            return None
        if pnl == 0:
            return self.capital_brl
        if brl_per_quote is None:
            return None
        return self.capital_brl + pnl * brl_per_quote

    def accepts(self, signal: Signal) -> bool:
        """Whether this agent trades on ``signal``: its own slot, above its strength bar."""

        if slot_of(signal) != self.slot:
            return False
        floor = self.params.get("min_strength")
        return floor is None or Decimal(str(signal.strength)) >= Decimal(str(floor))

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "sequence": self.sequence,
            "name": self.name,
            "slot": self.slot.to_dict(),
            "params": dict(self.params),
            "capital_brl": str(self.capital_brl),
            "status": self.status.value,
            "generation": self.generation,
            "created_at": self.created_at.isoformat(),
            "eliminated_at": self.eliminated_at.isoformat() if self.eliminated_at else None,
            "elimination_reason": self.elimination_reason,
            "book": {
                "position_quantity": str(self.book.quantity),
                "position_cost": str(self.book.cost),
                "realized_pnl": str(self.book.realized_pnl),
                "fees": str(self.book.fees),
            },
        }


def agent_code(sequence: int) -> tuple[str, str]:
    name = NAMES[(sequence - 1) % len(NAMES)]
    return f"AG-{sequence:04d}-{name}", name


# --- sizing ------------------------------------------------------------------------------


def _floor_to_lot(quantity: Decimal, instrument: TradableInstrument) -> Decimal:
    lots = (quantity / instrument.lot_size).to_integral_value(rounding=ROUND_DOWN)
    return lots * instrument.lot_size


@dataclass(frozen=True)
class AgentSizing:
    intent: TradeIntent | None
    reason: str | None
    sizing: dict[str, str]


def shape_for_agent(
    intent: TradeIntent,
    *,
    agent: TraderAgent,
    instrument: TradableInstrument,
    equity_brl: Decimal,
    brl_per_quote: Decimal,
) -> AgentSizing:
    """Shrink a reviewed proposal to what this agent may trade. Never enlarges it.

    A SELL closes only the agent's own position (never the account's other holdings). A BUY is
    scaled by the survival multiplier and capped so the agent's exposure stays within 1.0x of
    its own BRL equity.
    """

    price = intent.limit_price
    held = agent.book.quantity
    sizing: dict[str, object] = {
        "agent_id": agent.agent_id,
        "agent_equity_brl": equity_brl,
        "agent_position": held,
    }

    def refuse(reason: str) -> AgentSizing:
        return AgentSizing(None, reason, {k: str(v) for k, v in sizing.items()})

    if price is None or brl_per_quote <= 0:
        return refuse("agent needs a limit price and a BRL rate")
    if intent.side == "SELL":
        quantity = _floor_to_lot(min(intent.quantity, held), instrument)
        if quantity <= 0:
            return refuse("agent holds no position to sell")
    else:
        multiplier = size_multiplier(equity_brl)
        budget = equity_brl * AGENT_MAX_LEVERAGE / brl_per_quote
        headroom = budget - held * price
        sizing.update(survival_multiplier=multiplier, agent_headroom_quote=headroom)
        if headroom <= 0:
            return refuse("agent exposure cap reached")
        quantity = _floor_to_lot(min(intent.quantity * multiplier, headroom / price), instrument)
        if quantity <= 0:
            return refuse("agent size rounds to zero")
    quantity = min(quantity, intent.quantity)
    violation = instrument.order_violation(quantity, price)
    if violation is not None:
        sizing["quantity"] = quantity
        return refuse(f"agent size: {violation}")
    sizing["quantity"] = quantity
    shaped = intent.model_copy(update={"quantity": quantity})
    return AgentSizing(shaped, None, {k: str(v) for k, v in sizing.items()})


# --- durable roster ----------------------------------------------------------------------

roster_metadata = MetaData()
_DECIMAL = String(64)  # exact text: SQLite's NUMERIC goes through float

agents_table = Table(
    "agent_roster",
    roster_metadata,
    Column("agent_id", String(40), primary_key=True),
    Column("sequence", Integer, nullable=False),
    Column("name", String(40), nullable=False),
    Column("slot_kind", String(40), nullable=False),
    Column("instrument_id", String(160), nullable=False, index=True),
    Column("params", Text, nullable=False),
    Column("capital_brl", _DECIMAL, nullable=False),
    Column("status", String(16), nullable=False),
    Column("generation", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("eliminated_at", DateTime(timezone=True), nullable=True),
    Column("elimination_reason", String(200), nullable=True),
    Column("position_quantity", _DECIMAL, nullable=False),
    Column("position_cost", _DECIMAL, nullable=False),
    Column("realized_pnl", _DECIMAL, nullable=False),
    Column("fees", _DECIMAL, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("sequence", name="uq_agent_roster_sequence"),
)

agent_orders_table = Table(
    "agent_orders",
    roster_metadata,
    Column("intent_id", String(36), primary_key=True),
    Column("agent_id", String(40), nullable=False, index=True),
    Column("client_order_id", String(80), nullable=True),
    Column("instrument_id", String(160), nullable=False),
    Column("side", String(4), nullable=False),
    Column("applied_quantity", _DECIMAL, nullable=False),
    Column("applied_notional", _DECIMAL, nullable=False),
    Column("applied_fee", _DECIMAL, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


class AllocationRefused(Exception):
    """Spawning would allocate more than the reference capital."""


class AgentEliminated(Exception):
    """The agent code is eliminated (or unknown): it can never trade again."""


@dataclass
class _Attribution:
    intent_id: str
    agent_id: str
    instrument_id: str
    side: str
    client_order_id: str | None = None
    applied_quantity: Decimal = Decimal("0")
    applied_notional: Decimal = Decimal("0")
    applied_fee: Decimal = Decimal("0")


Recorder = Callable[[str, dict[str, Any], str], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _dec(value: object, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value)) if value not in (None, "") else Decimal(default)
    except InvalidOperation:
        return Decimal(default)


class AgentRoster:
    """Agents, their books and the intent -> agent attribution, durable when given an engine."""

    def __init__(
        self,
        engine: AsyncEngine | None = None,
        *,
        recorder: Recorder | None = None,
        slots: Iterable[Slot] = INITIAL_SLOTS,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._engine = engine
        self._recorder = recorder
        self._slots = tuple(slots)
        self._clock = clock
        self._agents: dict[str, TraderAgent] = {}
        self._attributions: dict[str, _Attribution] = {}
        self._loaded = engine is None
        self._lock = asyncio.Lock()

    # -- reads (from the loaded cache) --

    @property
    def slots(self) -> tuple[Slot, ...]:
        return self._slots

    def agents(self) -> list[TraderAgent]:
        return sorted(self._agents.values(), key=lambda a: a.sequence)

    def get(self, agent_id: str) -> TraderAgent | None:
        return self._agents.get(agent_id)

    def active_for(self, slot: Slot) -> TraderAgent | None:
        for agent in self._agents.values():
            if agent.active and agent.slot == slot:
                return agent
        return None

    def admit(self, agent_id: str) -> bool:
        """Only an ACTIVE agent may send a proposal on. Eliminated or unknown: never."""

        agent = self._agents.get(agent_id)
        return agent is not None and agent.active

    def allocated(self) -> Decimal:
        return sum((a.capital_brl for a in self._agents.values() if a.active), Decimal("0"))

    def agent_for_intent(self, intent_id: str) -> str | None:
        found = self._attributions.get(intent_id)
        return found.agent_id if found else None

    # -- persistence --

    async def load(self) -> None:
        async with self._lock:
            await self._ensure_loaded()

    async def _ensure_loaded(self) -> None:
        if self._loaded or self._engine is None:
            return
        async with self._engine.begin() as connection:
            await connection.run_sync(roster_metadata.create_all)
            agents = (await connection.execute(select(agents_table))).mappings().all()
            orders = (await connection.execute(select(agent_orders_table))).mappings().all()
        for row in agents:
            agent = TraderAgent(
                agent_id=row["agent_id"],
                sequence=row["sequence"],
                name=row["name"],
                slot=Slot(SlotKind(row["slot_kind"]), row["instrument_id"]),
                params=json.loads(row["params"]),
                capital_brl=Decimal(row["capital_brl"]),
                status=AgentStatus(row["status"]),
                generation=row["generation"],
                created_at=row["created_at"],
                eliminated_at=row["eliminated_at"],
                elimination_reason=row["elimination_reason"],
                book=AgentBook(
                    quantity=Decimal(row["position_quantity"]),
                    cost=Decimal(row["position_cost"]),
                    realized_pnl=Decimal(row["realized_pnl"]),
                    fees=Decimal(row["fees"]),
                ),
            )
            self._agents[agent.agent_id] = agent
        for row in orders:
            self._attributions[row["intent_id"]] = _Attribution(
                intent_id=row["intent_id"],
                agent_id=row["agent_id"],
                instrument_id=row["instrument_id"],
                side=row["side"],
                client_order_id=row["client_order_id"],
                applied_quantity=Decimal(row["applied_quantity"]),
                applied_notional=Decimal(row["applied_notional"]),
                applied_fee=Decimal(row["applied_fee"]),
            )
        self._loaded = True

    def _agent_row(self, agent: TraderAgent) -> dict[str, Any]:
        return {
            "sequence": agent.sequence,
            "name": agent.name,
            "slot_kind": agent.slot.kind.value,
            "instrument_id": agent.slot.instrument_id,
            "params": json.dumps(agent.params, sort_keys=True),
            "capital_brl": str(agent.capital_brl),
            "status": agent.status.value,
            "generation": agent.generation,
            "created_at": agent.created_at,
            "eliminated_at": agent.eliminated_at,
            "elimination_reason": agent.elimination_reason,
            "position_quantity": str(agent.book.quantity),
            "position_cost": str(agent.book.cost),
            "realized_pnl": str(agent.book.realized_pnl),
            "fees": str(agent.book.fees),
            "updated_at": self._clock(),
        }

    def _order_row(self, attribution: _Attribution) -> dict[str, Any]:
        return {
            "agent_id": attribution.agent_id,
            "client_order_id": attribution.client_order_id,
            "instrument_id": attribution.instrument_id,
            "side": attribution.side,
            "applied_quantity": str(attribution.applied_quantity),
            "applied_notional": str(attribution.applied_notional),
            "applied_fee": str(attribution.applied_fee),
            "updated_at": self._clock(),
        }

    async def _write(
        self,
        *,
        insert_agent: TraderAgent | None = None,
        update_agents: Iterable[TraderAgent] = (),
        insert_order: _Attribution | None = None,
        update_order: _Attribution | None = None,
    ) -> None:
        """One transaction per state change: the book and its applied fill move together."""

        if self._engine is None:
            return
        async with self._engine.begin() as connection:
            if insert_agent is not None:
                await connection.execute(
                    agents_table.insert().values(
                        agent_id=insert_agent.agent_id, **self._agent_row(insert_agent)
                    )
                )
            for agent in update_agents:
                await connection.execute(
                    update(agents_table)
                    .where(agents_table.c.agent_id == agent.agent_id)
                    .values(**self._agent_row(agent))
                )
            if insert_order is not None:
                await connection.execute(
                    agent_orders_table.insert().values(
                        intent_id=insert_order.intent_id,
                        created_at=self._clock(),
                        **self._order_row(insert_order),
                    )
                )
            if update_order is not None:
                await connection.execute(
                    update(agent_orders_table)
                    .where(agent_orders_table.c.intent_id == update_order.intent_id)
                    .values(**self._order_row(update_order))
                )

    async def _journal(self, event_type: str, payload: dict[str, Any], key: str) -> None:
        if self._recorder is None:
            return
        try:
            await self._recorder(event_type, {**payload, **_BOUNDARY}, key)
        except Exception:  # the state change is durable; a journal outage must not undo it
            logger.exception("%s was not journaled", event_type)

    # -- lifecycle --

    async def seed(self) -> list[TraderAgent]:
        """Give every slot without an ACTIVE agent one. Idempotent."""

        spawned: list[TraderAgent] = []
        async with self._lock:
            await self._ensure_loaded()
            for slot in self._slots:
                if self._active(slot) is None:
                    try:
                        spawned.append(await self._spawn(slot))
                    except AllocationRefused:
                        logger.warning("no capital left to seed %s", slot.key)
        return spawned

    async def spawn(self, slot: Slot) -> TraderAgent:
        async with self._lock:
            await self._ensure_loaded()
            if self._active(slot) is not None:
                raise ValueError(f"slot {slot.key} already has an ACTIVE agent")
            return await self._spawn(slot)

    def _active(self, slot: Slot) -> TraderAgent | None:
        return self.active_for(slot)

    async def _spawn(self, slot: Slot, *, replaces: str | None = None) -> TraderAgent:
        if self.allocated() + AGENT_STARTING_CAPITAL > REFERENCE_CAPITAL:
            raise AllocationRefused(
                f"allocated R${self.allocated()} + R${AGENT_STARTING_CAPITAL} "
                f"exceeds the R${REFERENCE_CAPITAL} reference capital"
            )
        sequence = max((a.sequence for a in self._agents.values()), default=0) + 1
        generation = sum(1 for a in self._agents.values() if a.slot == slot)
        agent_id, name = agent_code(sequence)
        agent = TraderAgent(
            agent_id=agent_id,
            sequence=sequence,
            name=name,
            slot=slot,
            params=params_for(slot.kind, generation),
            capital_brl=AGENT_STARTING_CAPITAL,
            status=AgentStatus.ACTIVE,
            generation=generation,
            created_at=self._clock(),
        )
        await self._write(insert_agent=agent)
        self._agents[agent_id] = agent
        await self._journal(
            "AGENT_SPAWNED",
            {**agent.to_dict(), "replaces": replaces, "allocated_brl": str(self.allocated())},
            f"agent-spawned:{agent_id}",
        )
        return agent

    async def eliminate(
        self, agent_id: str, *, reason: str, equity_brl: Decimal | None = None
    ) -> tuple[TraderAgent, TraderAgent | None]:
        """Retire an agent for good and give its slot a fresh replacement (if capital allows)."""

        async with self._lock:
            await self._ensure_loaded()
            agent = self._agents.get(agent_id)
            if agent is None or not agent.active:
                raise AgentEliminated(agent_id)
            agent.status = AgentStatus.ELIMINATED
            agent.eliminated_at = self._clock()
            agent.elimination_reason = reason[:200]
            try:
                await self._write(update_agents=[agent])
            except Exception:
                agent.status, agent.eliminated_at, agent.elimination_reason = (
                    AgentStatus.ACTIVE,
                    None,
                    None,
                )
                raise
            await self._journal(
                "AGENT_ELIMINATED",
                {
                    **agent.to_dict(),
                    "equity_brl": str(equity_brl) if equity_brl is not None else None,
                },
                f"agent-eliminated:{agent_id}",
            )
            replacement: TraderAgent | None = None
            try:
                replacement = await self._spawn(agent.slot, replaces=agent_id)
            except AllocationRefused:
                logger.warning("no capital left to replace %s", agent_id)
            return agent, replacement

    # -- attribution --

    async def attribute(
        self,
        intent_id: str,
        agent_id: str,
        *,
        instrument_id: str,
        side: str,
        client_order_id: str | None = None,
    ) -> None:
        """Record that ``intent_id`` belongs to ``agent_id``. Refused for a non-ACTIVE agent."""

        async with self._lock:
            await self._ensure_loaded()
            if not self.admit(agent_id):
                raise AgentEliminated(agent_id)
            if intent_id in self._attributions:
                return
            attribution = _Attribution(
                intent_id=intent_id,
                agent_id=agent_id,
                instrument_id=instrument_id,
                side=side,
                client_order_id=client_order_id,
            )
            await self._write(insert_order=attribution)
            self._attributions[intent_id] = attribution

    async def apply_order(
        self, order: Mapping[str, Any], *, instrument: TradableInstrument | None = None
    ) -> list[dict[str, Any]]:
        """Fold an OMS order's cumulative fills into its agent's book. Idempotent per fill.

        Only the part not applied yet (cumulative filled quantity, notional and fee minus what
        was applied) moves the book, so reading the same order twice changes nothing.
        """

        intent_id = str(order.get("intent_id") or "")
        async with self._lock:
            await self._ensure_loaded()
            attribution = self._attributions.get(intent_id)
            if attribution is None:
                return []
            agent = self._agents.get(attribution.agent_id)
            if agent is None:
                return []
            filled = _dec(order.get("filled_quantity"))
            if order.get("filled_notional") not in (None, ""):
                notional = _dec(order.get("filled_notional"))
            else:
                notional = filled * _dec(order.get("average_price")) if filled else Decimal("0")
            fee = _dec(order.get("fee"))
            delta_q = filled - attribution.applied_quantity
            delta_n = notional - attribution.applied_notional
            delta_f = fee - attribution.applied_fee
            if delta_q < 0 or delta_f < 0:
                logger.error("order %s went backwards; not applied", intent_id)
                return []
            if delta_q == 0 and delta_f == 0:
                return []
            fee_asset = order.get("fee_asset")
            fee_base = Decimal("0")
            fee_quote = delta_f
            if instrument is not None and fee_asset == instrument.base_asset:
                fee_base, fee_quote = delta_f, Decimal("0")
            side = str(order.get("side") or attribution.side)
            before = AgentBook(**vars(agent.book))
            realized = agent.book.apply_fill(
                side, delta_q, delta_n, fee_quote=fee_quote, fee_base=fee_base
            )
            updated = _Attribution(
                **{
                    **vars(attribution),
                    "client_order_id": order.get("client_order_id") or attribution.client_order_id,
                    "applied_quantity": filled,
                    "applied_notional": notional,
                    "applied_fee": fee,
                }
            )
            try:
                await self._write(update_agents=[agent], update_order=updated)
            except Exception:
                agent.book = before
                raise
            self._attributions[intent_id] = updated
            payload = {
                "agent_id": agent.agent_id,
                "intent_id": intent_id,
                "client_order_id": updated.client_order_id,
                "instrument_id": attribution.instrument_id,
                "side": side,
                "quantity": str(delta_q),
                "notional": str(delta_n),
                "fee": str(delta_f),
                "fee_asset": fee_asset,
                "realized_pnl": str(realized),
                "book": agent.to_dict()["book"],
            }
        await self._journal(
            "AGENT_TRADE_ATTRIBUTED", payload, f"agent-fill:{intent_id}:{filled}:{fee}"
        )
        return [payload]
