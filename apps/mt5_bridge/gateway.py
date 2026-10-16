"""The thin seam between the bridge and the MetaTrader 5 terminal.

`Mt5Gateway` is what the bridge needs from MT5. `TerminalGateway` implements it with the
official `MetaTrader5` package, which only runs on Windows next to a logged-in terminal; tests
use a fake. Values are plain dicts and ints so nothing here depends on the package at import.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

# MetaTrader 5 constants (MQL5 reference), kept as ints so the bridge imports without MT5.
ACCOUNT_TRADE_MODE_DEMO = 0
ACCOUNT_TRADE_MODE_CONTEST = 1
ACCOUNT_TRADE_MODE_REAL = 2

TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_REMOVE = 8

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3

ORDER_TIME_GTC = 0
ORDER_TIME_DAY = 1

ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2

ORDER_STATE_STARTED = 0
ORDER_STATE_PLACED = 1
ORDER_STATE_CANCELED = 2
ORDER_STATE_PARTIAL = 3
ORDER_STATE_FILLED = 4
ORDER_STATE_REJECTED = 5
ORDER_STATE_EXPIRED = 6

TRADE_RETCODE_PLACED = 10008
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_DONE_PARTIAL = 10010
ACCEPTED_RETCODES = frozenset(
    {TRADE_RETCODE_PLACED, TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL}
)


class Mt5Gateway(Protocol):
    def account(self) -> dict[str, Any] | None: ...

    def symbol(self, name: str) -> dict[str, Any] | None: ...

    def tick(self, name: str) -> dict[str, Any] | None: ...

    def positions(self) -> list[dict[str, Any]]: ...

    def send(self, request: dict[str, Any]) -> dict[str, Any]: ...

    def active_order(self, ticket: int) -> dict[str, Any] | None: ...

    def history_order(self, ticket: int) -> dict[str, Any] | None: ...

    def deals(self, order_ticket: int) -> list[dict[str, Any]]: ...


class TerminalGateway:
    """The real terminal. Logs in on construction; credentials never leave this process."""

    def __init__(self, *, login: int, password: str, server: str, path: str | None = None) -> None:
        import MetaTrader5 as mt5  # Windows only

        self._mt5 = mt5
        kwargs: dict[str, Any] = {"login": login, "password": password, "server": server}
        if path:
            kwargs["path"] = path
        if not mt5.initialize(**kwargs):
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    @staticmethod
    def _dict(value: Any) -> dict[str, Any] | None:
        return value._asdict() if value is not None else None

    def account(self) -> dict[str, Any] | None:
        return self._dict(self._mt5.account_info())

    def symbol(self, name: str) -> dict[str, Any] | None:
        self._mt5.symbol_select(name, True)
        return self._dict(self._mt5.symbol_info(name))

    def tick(self, name: str) -> dict[str, Any] | None:
        return self._dict(self._mt5.symbol_info_tick(name))

    def positions(self) -> list[dict[str, Any]]:
        return [p._asdict() for p in self._mt5.positions_get() or ()]

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        result = self._mt5.order_send(request)
        if result is None:
            return {"retcode": -1, "order": 0, "comment": str(self._mt5.last_error())}
        return result._asdict()

    def active_order(self, ticket: int) -> dict[str, Any] | None:
        found = self._mt5.orders_get(ticket=ticket)
        return found[0]._asdict() if found else None

    def history_order(self, ticket: int) -> dict[str, Any] | None:
        found = self._mt5.history_orders_get(ticket=ticket)
        return found[0]._asdict() if found else None

    def deals(self, order_ticket: int) -> list[dict[str, Any]]:
        # Deals are looked up by time window; keep the ones of this order.
        now = datetime.now(UTC)
        found = self._mt5.history_deals_get(now - timedelta(days=7), now + timedelta(days=1)) or ()
        return [d._asdict() for d in found if d.order == order_ticket]
