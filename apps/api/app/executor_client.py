"""Client for the isolated executor (Phase 5b), over the private network.

The public API never holds venue credentials. It sends the executor an
intent plus its Risk Permit and reads back orders, balances and
reconciliation. A transport failure on dispatch leaves the outcome unknown:
retrying the same intent is safe, because the executor returns the order it
already holds for that intent instead of sending a second one.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from services.execution import RiskPermit, TradeIntent

# A venue reached over a link that can drop (the XP node on a PC over Tailscale) must fail fast:
# with the default 15 s, a switched-off PC would stall every view that touches both venues.
SHORT_TIMEOUT = httpx.Timeout(4.0, connect=2.0)
_ORDER_ID = re.compile(r"^[A-Za-z0-9_-]{1,36}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{1,20}$")


class ExecutorUnavailable(RuntimeError):
    """The executor could not be reached or failed; the request outcome is unknown."""


class ExecutorRefused(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


class ExecutorClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.AsyncClient(timeout=timeout or httpx.Timeout(15.0))

    async def _call(self, method: str, path: str, *, json: Any = None) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method, f"{self._base}{path}", json=json, headers=self._headers
            )
        except httpx.TransportError as exc:
            raise ExecutorUnavailable(f"{method} {path}: {type(exc).__name__}") from exc
        if response.status_code >= 500:
            raise ExecutorUnavailable(f"{method} {path}: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise ExecutorUnavailable(f"{method} {path}: unreadable response") from exc
        if response.status_code >= 400:
            detail = body.get("detail") if isinstance(body, dict) else None
            raise ExecutorRefused(response.status_code, str(detail))
        return body

    async def submit(self, intent: TradeIntent, permit: RiskPermit) -> dict[str, Any]:
        return await self._call(
            "POST",
            "/v1/orders",
            json={
                "intent": intent.model_dump(mode="json"),
                "permit": permit.model_dump(mode="json"),
            },
        )

    async def health(self) -> dict[str, Any]:
        """Public health: ``ok``, ``starting`` or ``halted`` (no reason, no token needed)."""

        return await self._call("GET", "/health")

    async def orders(self, limit: int = 50) -> dict[str, Any]:
        return await self._call("GET", f"/v1/orders?limit={int(limit)}")

    async def ledger(self) -> dict[str, Any]:
        return await self._call("GET", "/v1/ledger")

    async def quotes(self, names: list[str]) -> dict[str, Any]:
        """Venue quotes (XP_B3: read from the MT5 terminal by the executor)."""

        if not names or not all(_SYMBOL.match(name) for name in names):
            raise ValueError("names must be upper-case venue symbols")
        return await self._call("GET", f"/v1/quotes?names={','.join(names)}")

    async def reconcile(self) -> dict[str, Any]:
        return await self._call("POST", "/v1/reconcile")

    async def release_halt(self) -> dict[str, Any]:
        """Operator action, relayed. The executor refuses it while still diverged."""

        return await self._call("POST", "/v1/halt/release")

    async def order_action(self, client_order_id: str, action: str) -> dict[str, Any]:
        if action not in {"resolve", "cancel"}:
            raise ValueError("action must be resolve or cancel")
        if not _ORDER_ID.match(client_order_id):
            raise ExecutorRefused(404, "unknown order")
        return await self._call("POST", f"/v1/orders/{client_order_id}/{action}")

    async def aclose(self) -> None:
        await self._client.aclose()
