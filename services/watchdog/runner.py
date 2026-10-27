"""The watchdog loop: probe, decide, halt, report.

It talks to the public API and to the executor over HTTP only, each with its own watchdog token.
Those tokens can halt and report a heartbeat; they cannot place, release or change anything else.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from .checks import Checks, Limits, Observation

logger = logging.getLogger("proto.watchdog")

API_TOKEN_HEADER = "X-Proto-Watchdog-Token"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class CycleResult:
    at: datetime
    trips: list[str]
    status: dict[str, str]
    api_halted: bool
    executor_halted: bool


@dataclass
class Watchdog:
    watchdog_id: str
    api: httpx.AsyncClient  # base_url = public API
    api_token: str
    executor: httpx.AsyncClient | None = None  # base_url = executor, private network
    executor_token: str | None = None
    xp_executor: httpx.AsyncClient | None = None  # base_url = XP node, over Tailscale
    xp_executor_token: str | None = None
    alpaca_executor: httpx.AsyncClient | None = None  # base_url = Alpaca executor, private net
    alpaca_executor_token: str | None = None
    limits: Limits = field(default_factory=Limits)
    clock: Callable[[], datetime] = _utc_now
    _checks: Checks = field(init=False)
    _api_halted: bool = field(default=False, init=False)
    _executor_halted: bool = field(default=False, init=False)
    _xp_halted: bool = field(default=False, init=False)
    _alpaca_halted: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._checks = Checks(self.limits)

    async def _get(self, client: httpx.AsyncClient, path: str, **kw: Any) -> httpx.Response | None:
        try:
            return await client.get(path, timeout=5.0, **kw)
        except httpx.HTTPError as exc:
            logger.warning("probe %s%s failed: %s", client.base_url, path, exc)
            return None

    async def observe(self) -> Observation:
        health = await self._get(self.api, "/health")
        floor = await self._get(self.api, "/v1/floor/state", params={"limit": 1})
        market = await self._get(self.api, "/live/ready")
        executor = await self._status(self.executor, self.executor_token)
        xp = await self._status(self.xp_executor, self.xp_executor_token)
        alpaca = await self._status(self.alpaca_executor, self.alpaca_executor_token)
        return Observation(
            at=self.clock(),
            api_health=health is not None and health.status_code == 200,
            floor=floor.json() if floor is not None and floor.status_code == 200 else None,
            market_ready=None if market is None else market.status_code == 200,
            executor_configured=self.executor is not None,
            executor=executor,
            xp_configured=self.xp_executor is not None,
            xp=xp,
            alpaca_configured=self.alpaca_executor is not None,
            alpaca=alpaca,
        )

    async def _status(
        self, client: httpx.AsyncClient | None, token: str | None
    ) -> dict[str, Any] | None:
        if client is None:
            return None
        response = await self._get(
            client, "/v1/watchdog/status", headers={"Authorization": f"Bearer {token}"}
        )
        if response is not None and response.status_code == 200:
            return response.json()
        return None

    async def _halt_api(self, reasons: list[str], obs: Observation) -> None:
        controls = (obs.floor or {}).get("controls") or {}
        if self._api_halted and controls.get("global_kill"):
            return  # still halted from this incident
        try:
            response = await self.api.post(
                "/v1/watchdog/halt",
                json={"watchdog_id": self.watchdog_id, "reasons": reasons},
                headers={API_TOKEN_HEADER: self.api_token},
                timeout=5.0,
            )
            self._api_halted = response.status_code == 200
            if not self._api_halted:
                logger.error(
                    "api refused the watchdog halt: %s %s",
                    response.status_code,
                    response.text[:200],
                )
        except httpx.HTTPError as exc:
            self._api_halted = False
            logger.error("api halt failed: %s", exc)

    async def _halt_executor(self, reasons: list[str], obs: Observation) -> None:
        if self.executor is None:
            return
        if self._executor_halted and (obs.executor or {}).get("halted"):
            return
        try:
            response = await self.executor.post(
                "/v1/watchdog/halt",
                json={"watchdog_id": self.watchdog_id, "reasons": reasons},
                headers={"Authorization": f"Bearer {self.executor_token}"},
                timeout=5.0,
            )
            self._executor_halted = response.status_code == 200
        except httpx.HTTPError as exc:
            self._executor_halted = False
            logger.error("executor halt failed: %s", exc)

    async def _halt_xp(self, reasons: list[str], obs: Observation) -> None:
        if self.xp_executor is None:
            return
        if self._xp_halted and (obs.xp or {}).get("halted"):
            return
        try:
            response = await self.xp_executor.post(
                "/v1/watchdog/halt",
                json={"watchdog_id": self.watchdog_id, "reasons": reasons},
                headers={"Authorization": f"Bearer {self.xp_executor_token}"},
                timeout=5.0,
            )
            self._xp_halted = response.status_code == 200
        except httpx.HTTPError as exc:
            self._xp_halted = False
            logger.error("xp executor halt failed: %s", exc)

    async def _halt_alpaca(self, reasons: list[str], obs: Observation) -> None:
        if self.alpaca_executor is None:
            return
        if self._alpaca_halted and (obs.alpaca or {}).get("halted"):
            return
        try:
            response = await self.alpaca_executor.post(
                "/v1/watchdog/halt",
                json={"watchdog_id": self.watchdog_id, "reasons": reasons},
                headers={"Authorization": f"Bearer {self.alpaca_executor_token}"},
                timeout=5.0,
            )
            self._alpaca_halted = response.status_code == 200
        except httpx.HTTPError as exc:
            self._alpaca_halted = False
            logger.error("alpaca executor halt failed: %s", exc)

    async def _heartbeat(self, result: CycleResult) -> None:
        try:
            await self.api.post(
                "/v1/watchdog/heartbeat",
                json={
                    "watchdog_id": self.watchdog_id,
                    "trips": result.trips,
                    "checks": result.status,
                    "api_halted": result.api_halted,
                    "executor_halted": result.executor_halted,
                },
                headers={API_TOKEN_HEADER: self.api_token},
                timeout=5.0,
            )
        except httpx.HTTPError as exc:
            logger.warning("heartbeat failed: %s", exc)

    async def cycle(self) -> CycleResult:
        obs = await self.observe()
        trips, status = self._checks.evaluate(obs)
        if trips:
            # Halt every side we can reach, every cycle the condition persists, until it took.
            logger.error("watchdog tripped: %s", trips)
            await self._halt_api(trips, obs)
            await self._halt_executor(trips, obs)
        else:
            # A clear cycle ends the incident; halts stay until an operator releases them.
            self._api_halted = self._executor_halted = False
        # The XP node is its own incident: it halts the XP executor only.
        xp_trips, xp_status = self._checks.evaluate_xp(obs)
        if xp_trips:
            logger.error("watchdog tripped on the XP node: %s", xp_trips)
            await self._halt_xp(xp_trips, obs)
        else:
            self._xp_halted = False
        if self.xp_executor is not None:
            xp_status["xp_halted_by_watchdog"] = str(self._xp_halted).lower()
        # The Alpaca executor too: it halts the Alpaca executor only.
        us_trips, us_status = self._checks.evaluate_alpaca(obs)
        if us_trips:
            logger.error("watchdog tripped on the Alpaca executor: %s", us_trips)
            await self._halt_alpaca(us_trips, obs)
        else:
            self._alpaca_halted = False
        if self.alpaca_executor is not None:
            us_status["alpaca_halted_by_watchdog"] = str(self._alpaca_halted).lower()
        result = CycleResult(
            obs.at,
            trips + xp_trips + us_trips,
            {**status, **xp_status, **us_status},
            self._api_halted,
            self._executor_halted,
        )
        await self._heartbeat(result)
        return result

    async def run(self, interval: float, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            try:
                await self.cycle()
            except Exception:  # the loop must outlive any single bad cycle
                logger.exception("watchdog cycle failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass
