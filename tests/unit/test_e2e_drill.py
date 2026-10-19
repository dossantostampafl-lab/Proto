"""The closed-loop drill: a round trip through the permit chain and the orphan cleanup.

Everything is an offline fake: the executor, the Risk Engine verdicts, the account and the
agent roster. The fake OMS accepts an order only with the permit the fake Risk Engine issued for
that exact intent, once, so a drill that bypassed the permit could not trade here.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from apps.api.app import agent_surface, e2e_drill, oms_surface, risk_surface, roster_surface
from services.agents import build_exit
from services.execution.catalog import PAPER_INSTRUMENTS
from services.execution.venue import Quote

BTC = "BINANCE_SPOT:BTCUSDT"
BID, ASK = Decimal("60000"), Decimal("60001")
FAUCET = Decimal("1")


class FakeExecutor:
    """Orders by client id; each poll advances every order's status queue by one."""

    def __init__(
        self,
        statuses: list[str],
        *,
        consistent: bool = True,
        entry_filled: str = "0.0153",
        entry_fee_base: str = "0",
        exit_statuses: list[str] | None = None,
        exit_filled: str | None = None,
        own: str = "0",
    ) -> None:
        self.entry_statuses, self.entry_filled = statuses, Decimal(entry_filled)
        self.entry_fee_base = Decimal(entry_fee_base)
        self.exit_statuses = exit_statuses or ["FILLED"]
        self.exit_filled = exit_filled
        self.consistent = consistent
        self.book: dict[str, dict] = {}
        self.cancelled: list[tuple[str, str]] = []
        self.submitted: list = []  # (intent, permit) for every order that reached the venue
        # Venue balance: the faucet's opening coin plus PROTO's own position.
        self.balance = {"BTC": FAUCET + Decimal(own)}
        self.opening = {"BTC": FAUCET}

    def place(self, client_order_id: str, side: str, statuses: list[str], filled: Decimal):
        self.book[client_order_id] = {"side": side, "statuses": list(statuses), "filled": filled}
        sign = 1 if side == "BUY" else -1
        fee = self.entry_fee_base if side == "BUY" else Decimal("0")
        self.balance["BTC"] += sign * filled - fee
        return {"client_order_id": client_order_id, "status": "SUBMITTED", "side": side}

    async def orders(self, limit: int = 50):
        rows = []
        for client_order_id, order in self.book.items():
            queue = order["statuses"]
            status = queue.pop(0) if len(queue) > 1 else queue[0]
            rows.append(
                {
                    "client_order_id": client_order_id,
                    "status": status,
                    "side": order["side"],
                    "filled_quantity": str(order["filled"]),
                }
            )
        return {"orders": rows}

    async def order_action(self, client_order_id: str, action: str):
        self.cancelled.append((client_order_id, action))
        self.book[client_order_id]["statuses"] = ["CANCELED"]
        return {}

    def own(self) -> Decimal:
        return max(self.balance["BTC"] - self.opening["BTC"], Decimal("0"))


class FakeRoster:
    def __init__(self, agents) -> None:
        self._agents = agents

    async def load(self) -> None:
        return None

    def agents(self):
        return list(self._agents)


def agent(quantity: str, status: str = "ACTIVE", instrument_id: str = BTC):
    return SimpleNamespace(
        status=status,
        slot=SimpleNamespace(instrument_id=instrument_id),
        book=SimpleNamespace(quantity=Decimal(quantity)),
    )


def _wire(
    monkeypatch,
    executor: FakeExecutor,
    *,
    approved: bool = True,
    exit_approved: bool = True,
    agents=(),
    synced: bool = True,
):
    monkeypatch.setattr(
        e2e_drill,
        "settings",
        SimpleNamespace(oms_execution_backend="executor", live_market_source="BINANCE"),
    )
    monkeypatch.setattr(oms_surface, "executor_mode", lambda: True)
    monkeypatch.setattr(oms_surface, "executor_client", lambda: executor)

    async def reconcile():
        return {"consistent": executor.consistent, "divergences": []}

    monkeypatch.setattr(oms_surface, "reconcile", reconcile)
    monkeypatch.setattr(
        e2e_drill,
        "live_quote",
        lambda i, market_source: Quote(bid=BID, ask=ASK, observed_at=datetime.now(UTC)),
    )

    async def account_state(quote_asset: str):
        # Like the real one: the own position is the balance minus the opening holdings.
        positions = {
            i: (executor.own() if i == BTC else Decimal("0"))
            for i, instrument in PAPER_INSTRUMENTS.items()
            if instrument.quote_asset == quote_asset
        }
        return SimpleNamespace(positions=positions)

    monkeypatch.setattr(oms_surface, "account_state", account_state)

    world = SimpleNamespace(assessed=[], permits={}, journal=[], buffered=[])

    async def assess(intent):
        world.assessed.append(intent)
        ok = exit_approved if intent.side == "SELL" else approved
        # The real engine refuses a SELL that is not reduce-only or larger than the own position.
        if intent.side == "SELL" and (not intent.reduce_only or intent.quantity > executor.own()):
            ok = False
        if not ok:
            return SimpleNamespace(approved=False, rejections=("collar",), permit=None)
        permit = SimpleNamespace(intent_id=intent.intent_id, used=False)
        world.permits[intent.intent_id] = permit
        return SimpleNamespace(approved=True, rejections=(), permit=permit)

    async def execute(intent, permit):
        # The OMS consumes the permit of exactly this intent, once.
        assert world.permits.get(intent.intent_id) is permit and not permit.used
        permit.used = True
        executor.submitted.append((intent, permit))
        filled = (
            Decimal(executor.exit_filled) if executor.exit_filled is not None else intent.quantity
        )
        return executor.place(
            f"x{len(executor.submitted)}", intent.side, executor.exit_statuses, filled
        )

    monkeypatch.setattr(risk_surface, "assess", assess)
    monkeypatch.setattr(oms_surface, "execute", execute)

    monkeypatch.setattr(roster_surface, "roster", lambda: FakeRoster(agents))

    async def sync_fills():
        return synced

    monkeypatch.setattr(roster_surface, "sync_fills", sync_fills)

    monkeypatch.setattr(agent_surface, "buffer_signal", world.buffered.append)
    monkeypatch.setattr(agent_surface, "forget_signal", world.buffered.remove)

    async def evaluate(instrument_id):
        assert world.buffered and world.buffered[0].strategy_id == "e2e-drill"
        decision = SimpleNamespace(
            proposal=object(), outcome=SimpleNamespace(value="PROPOSED"), abstain_reason=None
        )
        risk = SimpleNamespace(approved=approved, rejections=() if approved else ("collar",))
        order = None
        if approved:
            order = executor.place("c1", "BUY", executor.entry_statuses, executor.entry_filled)
        return decision, risk, order

    monkeypatch.setattr(agent_surface, "evaluate", evaluate)

    async def journal(**event):
        world.journal.append(event)
        return SimpleNamespace(created=True)

    monkeypatch.setattr(e2e_drill, "record_operational_event", journal)
    return world


async def _no_sleep(_):
    return None


def _run() -> dict:
    return asyncio.run(e2e_drill.run_drill(sleep=_no_sleep))


def _step(report: dict, name: str) -> dict:
    return next(s for s in report["steps"] if s["step"] == name)


def _sells(executor: FakeExecutor) -> list:
    return [intent for intent, _ in executor.submitted if intent.side == "SELL"]


# ---------- round trip ----------


def test_the_drill_round_trip_sells_back_what_it_bought_and_passes(monkeypatch) -> None:
    executor = FakeExecutor(["ACKNOWLEDGED", "FILLED"], own="0.002")
    world = _wire(monkeypatch, executor, agents=[agent("0.002")])
    report = _run()
    assert report["verdict"] == "PASSED", report
    assert [s["step"] for s in report["steps"]] == [
        "executor_backend",
        "live_quote",
        "inventory_cleanup",
        "agent_decision",
        "risk_permit",
        "dispatched",
        "venue_order",
        "exit_permit",
        "exit_order",
        "flat",
        "reconciliation",
    ]
    [sell] = _sells(executor)
    assert sell.quantity == Decimal("0.0153") and sell.reduce_only and sell.side == "SELL"
    assert sell.instrument_id == BTC and sell.limit_price == BID
    assert _step(report, "flat")["residual"] == "0"
    # Back where it started: the agent's coins and the faucet's are untouched.
    assert executor.own() == Decimal("0.002") and executor.balance["BTC"] == FAUCET + Decimal(
        "0.002"
    )
    assert report["venue_environment"] == "TESTNET" and report["real_money_execution"] is False
    assert len(report["digest"]) == 64 and world.buffered == []
    assert executor.cancelled == []


def test_a_partial_entry_closes_only_the_filled_quantity(monkeypatch) -> None:
    executor = FakeExecutor(["ACKNOWLEDGED"], entry_filled="0.005")
    _wire(monkeypatch, executor, agents=[agent("0")])
    report = _run()
    assert executor.cancelled == [("c1", "cancel")]
    venue = _step(report, "venue_order")
    assert venue["status"] == "CANCELED" and venue["cancelled_after_wait"] is True
    [sell] = _sells(executor)
    assert sell.quantity == Decimal("0.005")
    assert report["verdict"] == "PASSED", report
    assert executor.own() == 0


def test_a_coin_fee_is_not_sold_twice(monkeypatch) -> None:
    """The fee taken in BTC is not the drill's to sell: the exit is the own position's growth."""

    executor = FakeExecutor(["FILLED"], entry_fee_base="0.0000153", own="0.004")
    _wire(monkeypatch, executor, synced=False)  # no attribution: the growth cap alone
    report = _run()
    [sell] = _sells(executor)
    assert sell.quantity == Decimal("0.01528")  # 0.0152847 floored to the lot
    assert executor.own() >= Decimal("0.004")  # the pre-existing coins stay
    assert report["verdict"] == "PASSED", report


def test_an_unfilled_entry_needs_no_exit(monkeypatch) -> None:
    executor = FakeExecutor(["ACKNOWLEDGED"], entry_filled="0")
    _wire(monkeypatch, executor, agents=[agent("0")])
    report = _run()
    assert _sells(executor) == []
    assert _step(report, "flat") == {
        "step": "flat",
        "ok": True,
        "entry_filled": "0",
        "exit_filled": "0",
        "residual": "0",
    }
    assert report["verdict"] == "PASSED"


def test_an_exit_refused_by_risk_fails_the_drill_and_nothing_is_sold(monkeypatch) -> None:
    executor = FakeExecutor(["FILLED"])
    world = _wire(monkeypatch, executor, exit_approved=False, agents=[agent("0")])
    report = _run()
    assert report["verdict"] == "FAILED"
    assert _step(report, "exit_permit")["ok"] is False
    assert _step(report, "exit_order")["ok"] is False and _step(report, "flat")["ok"] is False
    assert _sells(executor) == [] and any(i.side == "SELL" for i in world.assessed)
    assert report["steps"][-1]["step"] == "reconciliation"


def test_a_resting_exit_is_cancelled_and_the_drill_is_not_flat(monkeypatch) -> None:
    executor = FakeExecutor(["FILLED"], exit_statuses=["ACKNOWLEDGED"], exit_filled="0")
    _wire(monkeypatch, executor, agents=[agent("0")])
    report = _run()
    assert executor.cancelled == [("x1", "cancel")]
    exit_order = _step(report, "exit_order")
    assert exit_order["status"] == "CANCELED" and exit_order["cancelled_after_wait"] is True
    assert _step(report, "flat")["ok"] is False and report["verdict"] == "FAILED"


def test_a_refused_entry_permit_or_a_divergence_fails_the_drill(monkeypatch) -> None:
    _wire(monkeypatch, FakeExecutor(["FILLED"]), approved=False, agents=[agent("0")])
    refused = _run()
    assert refused["verdict"] == "FAILED" and refused["steps"][-1]["step"] == "risk_permit"
    _wire(monkeypatch, FakeExecutor(["FILLED"], consistent=False), agents=[agent("0")])
    diverged = _run()
    assert diverged["verdict"] == "FAILED" and diverged["steps"][-1] == {
        "step": "reconciliation",
        "ok": False,
        "divergences": 0,
    }
    assert _step(diverged, "flat")["ok"] is True  # the round trip itself completed
    assert refused["digest"] != diverged["digest"]
    e2e_drill.reset_drill_state()


# ---------- orphan cleanup ----------


def test_the_cleanup_sells_only_the_orphaned_drill_inventory(monkeypatch) -> None:
    # Own position 0.110 BTC: 0.002 + 0.001 in agent books (one eliminated), 0.107 orphaned.
    # The faucet's 1 BTC is outside the own position.
    executor = FakeExecutor(["FILLED"], own="0.110")
    world = _wire(monkeypatch, executor, agents=[agent("0.002"), agent("0.001", "ELIMINATED")])
    report = _run()
    assert report["verdict"] == "PASSED", report
    unwind, exit_sell = _sells(executor)
    assert unwind.quantity == Decimal("0.107") and unwind.reduce_only
    assert exit_sell.quantity == Decimal("0.0153")
    cleanup = _step(report, "inventory_cleanup")
    assert cleanup["ok"] is True and cleanup["unwound"][0]["quantity"] == "0.107"
    [event] = [e for e in world.journal if e["event_type"] == "DRILL_INVENTORY_UNWOUND"]
    assert event["payload"]["quantity"] == "0.107"
    assert event["payload"]["agent_quantity"] == "0.003"
    assert event["payload"]["real_money_execution"] is False
    # What is left is exactly the agents' coins, plus the untouched faucet.
    assert executor.own() == Decimal("0.003") and executor.balance["BTC"] == Decimal("1.003")


def test_the_cleanup_floors_to_the_lot_and_leaves_dust_below_the_minimum(monkeypatch) -> None:
    executor = FakeExecutor(["FILLED"], own="0.0020001")
    _wire(monkeypatch, executor, agents=[agent("0.002")])
    report = _run()
    assert [s.quantity for s in _sells(executor)] == [Decimal("0.0153")]  # only the exit
    assert _step(report, "inventory_cleanup")["unwound"] == []


def test_the_cleanup_is_skipped_without_attribution_data(monkeypatch) -> None:
    for wiring in ({"agents": []}, {"agents": [agent("0.002")], "synced": False}):
        executor = FakeExecutor(["FILLED"], own="0.107")
        _wire(monkeypatch, executor, **wiring)
        report = _run()
        cleanup = _step(report, "inventory_cleanup")
        assert cleanup["ok"] is True and cleanup["skipped"] == "agent attribution unavailable"
        # Only the round trip's own exit is sold; the 0.107 stays (never guessed).
        assert [s.quantity for s in _sells(executor)] == [Decimal("0.0153")]
        assert executor.own() == Decimal("0.107")


def test_an_unreadable_roster_is_unknown_not_empty(monkeypatch) -> None:
    executor = FakeExecutor(["FILLED"], own="0.107")
    _wire(monkeypatch, executor)

    def broken():
        raise RuntimeError("store down")

    monkeypatch.setattr(roster_surface, "roster", broken)
    report = _run()
    assert _step(report, "inventory_cleanup")["skipped"] == "agent attribution unavailable"
    assert [s.quantity for s in _sells(executor)] == [Decimal("0.0153")]


def test_a_refused_cleanup_stops_the_drill_before_any_new_entry(monkeypatch) -> None:
    executor = FakeExecutor(["FILLED"], own="0.107")
    world = _wire(monkeypatch, executor, exit_approved=False, agents=[agent("0")])
    report = _run()
    assert report["verdict"] == "FAILED"
    assert report["steps"][-1]["step"] == "inventory_cleanup"
    assert not any(i.side == "BUY" for i in world.assessed) and executor.submitted == []


# ---------- the permit chain ----------


def test_the_drill_never_calls_the_venue_directly() -> None:
    source = inspect.getsource(e2e_drill)
    assert ".submit(" not in source and "services.venues" not in source
    # The only executor action the drill takes itself is cancelling its own resting order.
    assert source.count("order_action(") == 1 and '"cancel"' in source


def test_build_exit_is_a_reduce_only_sell_capped_by_the_holding() -> None:
    instrument = PAPER_INSTRUMENTS[BTC]
    now = datetime.now(UTC)
    quote = Quote(bid=BID, ask=ASK, observed_at=now)
    ids = (uuid4(),)

    def exit_of(quantity: str, held: str, q: Quote | None = quote):
        return build_exit(
            instrument=instrument,
            quote=q,
            quantity=Decimal(quantity),
            position_quantity=Decimal(held),
            now=now,
            signal_ids=ids,
        )

    capped = exit_of("0.5", "0.0153").intent
    assert capped is not None and capped.quantity == Decimal("0.0153")
    assert capped.side == "SELL" and capped.reduce_only and capped.limit_price == BID
    assert exit_of("0.0123456", "1").intent.quantity == Decimal("0.01234")
    assert exit_of("0", "1").abstain_reason == "nothing to close"
    assert exit_of("0.01", "0").abstain_reason == "no position to close"
    assert exit_of("0.00001", "1").abstain_reason == "notional below venue minimum"
    assert exit_of("0.01", "1", None).abstain_reason == "no market data"
    stale = Quote(bid=BID, ask=ASK, observed_at=now - timedelta(minutes=1))
    assert exit_of("0.01", "1", stale).abstain_reason == "stale market data"
