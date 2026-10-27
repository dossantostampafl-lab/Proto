"""Private HTTP interface of the isolated executor.

Bind it to the private subnet only. Every route but `/health` requires the
bearer token the public API holds (`EXECUTOR_API_TOKEN`).

    uvicorn apps.executor.main:app --host <private-ip> --port 8100
"""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from services.execution import RiskPermit, TradeIntent
from services.oms import OrderRejected

from .runtime import Executor, ExecutorHalted
from .settings import ExecutorSettings

_bearer = HTTPBearer(auto_error=False)
Bearer = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
MAX_QUOTE_NAMES = 20


class ExecuteRequest(BaseModel):
    intent: TradeIntent
    permit: RiskPermit


def _boundary(executor: Executor) -> dict[str, object]:
    return {
        "venue_environment": executor.settings.environment.value,
        # Testnet funds are not money: no financial connectivity, no real-money execution.
        "financial_connectivity": False,
        "real_money_execution": False,
    }


def runtime(request: Request) -> Executor:
    return request.app.state.executor


def authorized(request: Request, credentials: Bearer) -> Executor:
    executor_ = runtime(request)
    expected = executor_.settings.api_token.get_secret_value()
    presented = credentials.credentials if credentials is not None else ""
    if not hmac.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(401, "invalid executor token")
    if not executor_.ready:
        raise HTTPException(503, "executor not ready")
    return executor_


Authorized = Annotated[Executor, Depends(authorized)]


def watchdog_authorized(request: Request, credentials: Bearer) -> Executor:
    """The watchdog's own token: health and halt only, never orders or releases."""

    executor_ = runtime(request)
    token = executor_.settings.watchdog_token
    if token is None:
        raise HTTPException(503, "watchdog token is not configured")
    presented = credentials.credentials if credentials is not None else ""
    if not hmac.compare_digest(presented.encode(), token.get_secret_value().encode()):
        raise HTTPException(401, "invalid watchdog token")
    return executor_


Watchdog = Annotated[Executor, Depends(watchdog_authorized)]


class WatchdogHalt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    watchdog_id: str = Field(min_length=1, max_length=40)
    reasons: list[str] = Field(min_length=1, max_length=50)


def create_app(executor: Executor | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        instance = executor or Executor(ExecutorSettings())
        app.state.executor = instance
        await instance.start()
        instance.start_background()
        try:
            yield
        finally:
            await instance.stop()

    app = FastAPI(title="PROTO executor", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health(request: Request) -> dict[str, object]:
        executor_ = runtime(request)
        status = "starting" if not executor_.ready else "halted" if executor_.halted else "ok"
        # Why it stopped and what diverged: operational facts, no credentials or tokens.
        return {
            "status": status,
            "halted_reason": executor_.halted,
            "divergences": executor_.last_divergences,
            **_boundary(executor_),
        }

    @app.post("/v1/orders")
    async def submit(body: ExecuteRequest, executor_: Authorized):
        try:
            order = await executor_.submit(body.intent, body.permit)
        except ExecutorHalted as exc:
            raise HTTPException(409, f"executor halted: {exc}") from exc
        except OrderRejected as exc:
            raise HTTPException(422, exc.code) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "order": order.to_dict(),
            "venue_halted": executor_.halted is not None,
            **_boundary(executor_),
        }

    @app.get("/v1/orders")
    async def orders(executor_: Authorized, limit: int = 50):
        rows = executor_._oms().orders[-max(1, min(limit, 500)) :]
        return {"orders": [o.to_dict() for o in reversed(rows)], **_boundary(executor_)}

    async def _act(executor_: Executor, client_order_id: str, action: str):
        oms = executor_._oms()
        if oms.get(client_order_id) is None:
            raise HTTPException(404, "unknown order")
        order = await getattr(oms, action)(client_order_id)
        await executor_.reconcile()
        return {"order": order.to_dict(), **_boundary(executor_)}

    @app.post("/v1/orders/{client_order_id}/resolve")
    async def resolve(client_order_id: str, executor_: Authorized):
        return await _act(executor_, client_order_id, "resolve_unknown")

    @app.post("/v1/orders/{client_order_id}/cancel")
    async def cancel(client_order_id: str, executor_: Authorized):
        return await _act(executor_, client_order_id, "cancel")

    @app.get("/v1/ledger")
    async def ledger(executor_: Authorized):
        assert executor_.ledger is not None
        balances = await executor_.ledger.balances()
        return {
            "account_id": executor_.settings.account_id,
            "balances": {asset: b.to_dict() for asset, b in balances.items()},
            "venue_halted": executor_.halted is not None,
            **_boundary(executor_),
        }

    @app.get("/v1/quotes")
    async def quotes(executor_: Authorized, names: str = ""):
        """Venue quotes for the public API's sizing and Risk Engine (XP_B3: from the terminal;
        ALPACA_US: Alpaca's free IEX feed, read with the executor's own keys)."""

        feed = getattr(executor_.adapter, "quotes", None)
        if feed is None:
            raise HTTPException(404, f"{executor_.venue.value} has no quote feed here")
        symbols = [n.strip().upper() for n in names.split(",") if n.strip()]
        if not symbols:
            raise HTTPException(422, "names is required")
        if len(symbols) > MAX_QUOTE_NAMES:
            raise HTTPException(422, f"at most {MAX_QUOTE_NAMES} names per request")
        configured = set(executor_.settings.symbol_list)
        unknown = sorted(set(symbols) - configured)
        if unknown:  # least privilege: only the symbols this executor was configured for
            raise HTTPException(422, f"not configured on this executor: {', '.join(unknown)}")
        return {"quotes": await feed(symbols), **_boundary(executor_)}

    @app.post("/v1/reconcile")
    async def reconcile(executor_: Authorized):
        return {**await executor_.reconcile(), **_boundary(executor_)}

    @app.post("/v1/halt/release")
    async def release(executor_: Authorized):
        try:
            result = await executor_.release_halt()
        except ExecutorHalted as exc:
            raise HTTPException(409, str(exc)) from exc
        return {**result, **_boundary(executor_)}

    @app.get("/v1/watchdog/status")
    async def watchdog_status(executor_: Watchdog):
        return {**executor_.watchdog_status(), **_boundary(executor_)}

    @app.post("/v1/watchdog/halt")
    async def watchdog_halt(body: WatchdogHalt, executor_: Watchdog):
        changed = executor_.watchdog_halt(body.watchdog_id, body.reasons)
        return {"halted": executor_.halted, "already_halted": not changed, **_boundary(executor_)}

    @app.get("/v1/events")
    async def events(executor_: Authorized):
        return {"events": list(executor_.events), **_boundary(executor_)}

    return app


def __getattr__(name: str):
    # `uvicorn apps.executor.main:app` builds the app (and reads settings) lazily,
    # so importing this module in tests never requires executor secrets.
    if name == "app":
        return create_app()
    raise AttributeError(name)
