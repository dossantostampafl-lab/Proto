"""MT5 bridge: a private HTTP API in front of the MetaTrader 5 terminal logged into XP.

It runs on the Windows VM in the private subnet and only the executor talks to it, with a bearer
token. It translates and never decides:

- **Demo only.** Every order call first reads the logged-in account. Anything but a DEMO account
  (contest or real) is refused with 403, so a real XP login cannot receive an order from PROTO
  even by mistake. Reads (health, symbols, quotes, balances) stay available.
- **Identity.** The OMS client order id is the key. The bridge stores which MT5 ticket it got,
  so a resubmitted id returns the order already placed instead of placing it twice.
- **Fills** come from the terminal's deals for that ticket; fees are the deals' commissions in
  the account currency.
"""

from __future__ import annotations

import hmac
import re
import sqlite3
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import gateway as g

_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,36}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{3,12}$")
MAGIC = 20261016  # tags PROTO's orders in the terminal


class OrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_order_id: str = Field(pattern=_CLIENT_ID.pattern)
    symbol: str = Field(pattern=_SYMBOL.pattern)
    side: Literal["BUY", "SELL"]
    quantity: Decimal = Field(gt=0)
    order_type: Literal["MARKET", "LIMIT"]
    limit_price: Decimal | None = Field(default=None, gt=0)
    time_in_force: Literal["GTC", "DAY", "IOC"] = "DAY"


class _Store:
    """client_order_id -> MT5 ticket, durable on the VM's disk."""

    def __init__(self, path: Path) -> None:
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS orders (client_order_id TEXT PRIMARY KEY, ticket INTEGER,"
            " symbol TEXT, side TEXT, quantity TEXT, created_at TEXT)"
        )
        self._db.commit()

    def get(self, client_order_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT ticket, symbol, side, quantity FROM orders WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        if row is None:
            return None
        return {"ticket": row[0], "symbol": row[1], "side": row[2], "quantity": row[3]}

    def put(self, client_order_id: str, ticket: int, request: OrderRequest) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO orders VALUES (?, ?, ?, ?, ?, ?)",
                (
                    client_order_id,
                    ticket,
                    request.symbol,
                    request.side,
                    str(request.quantity),
                    datetime.now(UTC).isoformat(),
                ),
            )
            self._db.commit()


def _account_mode(account: dict[str, Any] | None) -> str:
    if account is None:
        return "UNAVAILABLE"
    return {g.ACCOUNT_TRADE_MODE_DEMO: "DEMO", g.ACCOUNT_TRADE_MODE_CONTEST: "CONTEST"}.get(
        int(account.get("trade_mode", g.ACCOUNT_TRADE_MODE_REAL)), "REAL"
    )


def _time(value: Any) -> str:
    seconds = float(value or 0)
    stamp = datetime.fromtimestamp(seconds, UTC) if seconds else datetime.now(UTC)
    return stamp.isoformat()


def create_app(terminal: g.Mt5Gateway, *, token: str, store_path: Path) -> FastAPI:
    if len(token) < 32:
        raise ValueError("the bridge token must be at least 32 characters")
    expected = token.encode()
    store = _Store(store_path)
    app = FastAPI(title="PROTO MT5 bridge", docs_url=None, redoc_url=None)

    def authorized(authorization: str = Header(default="")) -> None:
        supplied = authorization.removeprefix("Bearer ").encode()
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(401, "unauthorized")

    def demo_only() -> dict[str, Any]:
        account = terminal.account()
        mode = _account_mode(account)
        if mode != "DEMO":
            raise HTTPException(
                403, f"orders refused: the terminal is logged into a {mode} account"
            )
        assert account is not None
        return account

    def report(client_order_id: str, saved: dict[str, Any]) -> dict[str, Any]:
        ticket = int(saved["ticket"])
        active = terminal.active_order(ticket)
        order = active or terminal.history_order(ticket)
        deals = [d for d in terminal.deals(ticket) if float(d.get("volume", 0)) > 0]
        filled = sum(Decimal(str(d["volume"])) for d in deals)
        notional = sum(Decimal(str(d["volume"])) * Decimal(str(d["price"])) for d in deals)
        fee = sum(abs(Decimal(str(d.get("commission", 0)))) for d in deals)
        quantity = Decimal(saved["quantity"])
        state = int(order["state"]) if order else None
        if filled >= quantity:
            status = "FILLED"
        elif state in (g.ORDER_STATE_CANCELED, g.ORDER_STATE_EXPIRED):
            status = "CANCELED"
        elif state == g.ORDER_STATE_REJECTED:
            status = "REJECTED"
        elif filled > 0:
            status = "PARTIALLY_FILLED"
        elif order is None:
            status = "UNKNOWN"
        else:
            status = "ACKNOWLEDGED"
        return {
            "client_order_id": client_order_id,
            "ticket": ticket,
            "symbol": saved["symbol"],
            "side": saved["side"],
            "status": status,
            "quantity": str(quantity),
            "filled_quantity": str(filled),
            "average_price": str(notional / filled) if filled else None,
            "fee": str(fee),
            "fee_asset": "BRL",
            "reported_at": _time(order.get("time_done") or order.get("time_setup"))
            if order
            else _time(0),
        }

    @app.get("/health")
    def health() -> dict[str, Any]:
        account = terminal.account()
        return {
            "connected": account is not None,
            "account_mode": _account_mode(account),
            "server": account.get("server") if account else None,
            "currency": account.get("currency") if account else None,
            "real_money_execution": False,
        }

    @app.get("/v1/symbols", dependencies=[Depends(authorized)])
    def symbols(names: str) -> dict[str, Any]:
        rows = {}
        for name in names.split(","):
            if not _SYMBOL.match(name):
                continue
            info = terminal.symbol(name)
            if info is None or not info.get("visible", True):
                continue
            rows[name] = {
                "tick_size": str(info["trade_tick_size"]),
                "volume_min": str(info["volume_min"]),
                "volume_step": str(info["volume_step"]),
                "currency": info.get("currency_profit", "BRL"),
            }
        return {"symbols": rows}

    @app.get("/v1/quotes", dependencies=[Depends(authorized)])
    def quotes(names: str) -> dict[str, Any]:
        rows = {}
        for name in names.split(","):
            tick = terminal.tick(name) if _SYMBOL.match(name) else None
            if tick and tick.get("bid") and tick.get("ask"):
                observed = float(tick.get("time_msc", 0)) / 1000 or float(tick.get("time", 0))
                rows[name] = {
                    "bid": str(tick["bid"]),
                    "ask": str(tick["ask"]),
                    "observed_at": _time(observed),
                }
        return {"quotes": rows}

    @app.get("/v1/balances", dependencies=[Depends(authorized)])
    def balances() -> dict[str, Any]:
        account = terminal.account()
        if account is None:
            raise HTTPException(503, "terminal unavailable")
        totals: dict[str, str] = {account.get("currency", "BRL"): str(account["balance"])}
        for position in terminal.positions():
            sign = 1 if int(position.get("type", 0)) == g.ORDER_TYPE_BUY else -1
            symbol = str(position["symbol"])
            totals[symbol] = str(
                Decimal(totals.get(symbol, "0")) + sign * Decimal(str(position["volume"]))
            )
        return {"balances": totals, "account_mode": _account_mode(account)}

    @app.post("/v1/orders", dependencies=[Depends(authorized)])
    def submit(request: OrderRequest) -> dict[str, Any]:
        demo_only()
        saved = store.get(request.client_order_id)
        if saved is not None:  # the same id never places a second order
            return report(request.client_order_id, saved)
        if (request.order_type == "LIMIT") != (request.limit_price is not None):
            raise HTTPException(422, "limit_price is required exactly for LIMIT orders")
        buying = request.side == "BUY"
        mt5_request: dict[str, Any] = {
            "symbol": request.symbol,
            "volume": float(request.quantity),
            "magic": MAGIC,
            "comment": request.client_order_id[:31],
        }
        if request.order_type == "LIMIT":
            mt5_request.update(
                action=g.TRADE_ACTION_PENDING,
                type=g.ORDER_TYPE_BUY_LIMIT if buying else g.ORDER_TYPE_SELL_LIMIT,
                price=float(request.limit_price or 0),
                type_time=g.ORDER_TIME_GTC if request.time_in_force == "GTC" else g.ORDER_TIME_DAY,
                type_filling=g.ORDER_FILLING_RETURN,
            )
        else:
            tick = terminal.tick(request.symbol)
            if not tick:
                raise HTTPException(409, "no quote for a market order")
            mt5_request.update(
                action=g.TRADE_ACTION_DEAL,
                type=g.ORDER_TYPE_BUY if buying else g.ORDER_TYPE_SELL,
                price=float(tick["ask"] if buying else tick["bid"]),
                type_filling=g.ORDER_FILLING_IOC
                if request.time_in_force == "IOC"
                else g.ORDER_FILLING_RETURN,
            )
        result = terminal.send(mt5_request)
        if int(result.get("retcode", -1)) not in g.ACCEPTED_RETCODES or not result.get("order"):
            return {
                "client_order_id": request.client_order_id,
                "status": "REJECTED",
                "symbol": request.symbol,
                "side": request.side,
                "quantity": str(request.quantity),
                "filled_quantity": "0",
                "reason": f"{result.get('retcode')}: {result.get('comment', '')}"[:200],
                "reported_at": _time(0),
            }
        store.put(request.client_order_id, int(result["order"]), request)
        return report(request.client_order_id, store.get(request.client_order_id) or {})

    @app.get("/v1/orders/{client_order_id}", dependencies=[Depends(authorized)])
    def query(client_order_id: str) -> dict[str, Any]:
        saved = store.get(client_order_id)
        if saved is None:
            raise HTTPException(404, "unknown order")
        return report(client_order_id, saved)

    @app.post("/v1/orders/{client_order_id}/cancel", dependencies=[Depends(authorized)])
    def cancel(client_order_id: str) -> dict[str, Any]:
        demo_only()
        saved = store.get(client_order_id)
        if saved is None:
            raise HTTPException(404, "unknown order")
        if terminal.active_order(int(saved["ticket"])) is not None:
            terminal.send({"action": g.TRADE_ACTION_REMOVE, "order": int(saved["ticket"])})
        return report(client_order_id, saved)

    return app


def main() -> None:  # pragma: no cover - runs on the Windows VM
    import os

    import uvicorn

    terminal = g.TerminalGateway(
        login=int(os.environ["MT5_LOGIN"]),
        password=os.environ.get("MT5_PASSWORD") or None,  # unset: attach to the logged-in terminal
        server=os.environ["MT5_SERVER"],
        path=os.environ.get("MT5_PATH"),
    )
    store = Path(os.environ.get("MT5_BRIDGE_STATE", "C:/proto/mt5-bridge.sqlite3"))
    app = create_app(terminal, token=os.environ["MT5_BRIDGE_TOKEN"], store_path=store)
    uvicorn.run(
        app,
        host=os.environ.get("MT5_BRIDGE_HOST", "0.0.0.0"),
        port=int(os.environ.get("MT5_BRIDGE_PORT", "8200")),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
